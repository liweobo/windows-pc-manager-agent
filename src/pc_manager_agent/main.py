"""Application command-line and GUI entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from pc_manager_agent import __version__
from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.config.production import (
    BuildMode,
    FeatureFlags,
    ProductionConfigurationError,
    ProductionConfigValidator,
    ProductionRuntimeContext,
)
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.observability.crash import (
    CrashEvidenceError,
    CrashLoopGuard,
    LocalCrashReporter,
)
from pc_manager_agent.observability.logging import LoggingRuntime, configure_application_logging
from pc_manager_agent.platform_support.windows.msi_uninstall import current_process_is_elevated
from pc_manager_agent.platform_support.windows.single_instance import QtSingleInstanceGuard
from pc_manager_agent.ui.main_window import MainWindow
from pc_manager_agent.ui.safe_mode_window import SafeModeWindow
from pc_manager_agent.ui.system_tray import SystemTrayController

_LOG = logging.getLogger(__name__)
_SMOKE_MAIN_ELEVATED_EXIT_CODE = 23
_SMOKE_STARTUP_FAILURE_EXIT_CODE = 24


def _smoke_failure_exit_code(error: Exception) -> int:
    """Return a stable unattended exit code without weakening production validation."""
    if isinstance(error, ProductionConfigurationError) and {
        violation.code for violation in error.report.violations
    } == {"MAIN_ELEVATED"}:
        return _SMOKE_MAIN_ELEVATED_EXIT_CODE
    return _SMOKE_STARTUP_FAILURE_EXIT_CODE


def build_parser() -> argparse.ArgumentParser:
    """Build the minimal test-friendly CLI surface."""
    parser = argparse.ArgumentParser(description="Windows PC Manager Agent")
    parser.add_argument(
        "--version",
        action="store_true",
        help="show the application version when a console is attached, then exit",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="create the GUI offscreen, initialize dependencies, then exit",
    )
    parser.add_argument(
        "--safe-mode",
        action="store_true",
        help="start with all optional capability domains and model providers disabled",
    )
    return parser


def run_application(settings: AppSettings, *, smoke_test: bool = False) -> int:
    """Construct and run one guarded Qt application instance."""
    existing_application = QApplication.instance()  # 检查当前进程是否已经创建了QApplication实例
    application = (
        cast(QApplication, existing_application)
        if existing_application is not None
        else QApplication(sys.argv[:1])
    )
    application.setApplicationName("Windows PC Manager Agent")
    application.setOrganizationName("liweobo")
    application.setQuitOnLastWindowClosed(False)  # False表示用户关闭最后一个gui窗口时程序不结束
    guard = QtSingleInstanceGuard()
    if not guard.acquire():
        """如果有旧的服务端，则执行return 0；如果没有就的服务端则跳过if"""
        return 0
    crash_guard = CrashLoopGuard(settings.data_directory)
    try:
        crash_decision = crash_guard.begin_session()
    except CrashEvidenceError as exc:
        QMessageBox.critical(None, "启动失败", f"无法建立安全启动状态：{type(exc).__name__}")
        guard.close()
        return 1
    if settings.safe_mode or crash_decision.safe_mode_recommended:
        settings = reduce_to_safe_mode(settings)
    logging_runtime: LoggingRuntime | None = None
    crash_reporter: LocalCrashReporter | None = None
    previous_exception_hook = None
    try:
        if settings.build_mode is BuildMode.PRODUCTION:
            ProductionConfigValidator().require_valid(
                settings,
                ProductionRuntimeContext(
                    frozen_binary=bool(getattr(sys, "frozen", False)),
                    process_elevated=current_process_is_elevated(),
                    executable_path=Path(sys.executable).resolve(strict=False),
                    active_environment_names=frozenset(
                        name.upper() for name, value in os.environ.items() if value
                    ),
                    smoke_test=smoke_test,
                ),
            )
        logging_runtime = configure_application_logging(
            settings.data_directory, settings.build_mode
        )
        crash_reporter = LocalCrashReporter(settings.data_directory, __version__)
        previous_exception_hook = crash_reporter.install(
            chain_previous=settings.build_mode is not BuildMode.PRODUCTION
        )
        _LOG.info(
            "Application startup began",
            extra={
                "component": "main",
                "event_code": "APPLICATION_STARTING",
                "details": {
                    "app_version": __version__,
                    "build_mode": settings.build_mode.value,
                    "safe_mode": settings.safe_mode,
                },
            },
        )
        runtime = ApplicationRuntime(settings)
    except Exception as exc:
        if logging_runtime is not None:
            _LOG.error(
                "Application startup failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={"component": "main", "event_code": "APPLICATION_START_FAILED"},
            )
        if crash_reporter is not None:
            try:
                crash_reporter.capture(type(exc), exc, exc.__traceback__)
            except CrashEvidenceError:
                _LOG.warning(
                    "Local crash evidence could not be written",
                    extra={"component": "main", "event_code": "CRASH_EVIDENCE_FAILED"},
                )
        if previous_exception_hook is not None:
            sys.excepthook = previous_exception_hook
        if logging_runtime is not None:
            logging_runtime.close()
        guard.close()
        if smoke_test:
            return _smoke_failure_exit_code(exc)
        detail = str(exc) if settings.build_mode is not BuildMode.PRODUCTION else type(exc).__name__
        QMessageBox.critical(None, "启动失败", f"安全启动检查或本地数据库初始化失败：{detail}")
        return 1
    window = SafeModeWindow(runtime) if settings.safe_mode else MainWindow(runtime)

    def controlled_quit() -> None:
        if window.request_quit():
            application.quit()

    tray = SystemTrayController(window, controlled_quit)
    window.attach_tray(tray)
    tray.show()
    guard.server.newConnection.connect(tray.show_window)
    window.show()
    if smoke_test:
        QTimer.singleShot(100, controlled_quit)
    try:
        return application.exec()
    finally:
        window.shutdown()
        tray.hide()
        runtime.close()
        guard.close()
        if previous_exception_hook is not None:
            sys.excepthook = previous_exception_hook
        try:
            crash_guard.mark_clean_exit()
        except CrashEvidenceError:
            _LOG.error(
                "Clean-exit health state could not be written",
                extra={"component": "main", "event_code": "CLEAN_EXIT_STATE_FAILED"},
            )
        _LOG.info(
            "Application stopped normally",
            extra={"component": "main", "event_code": "APPLICATION_STOPPED"},
        )
        if logging_runtime is not None:
            logging_runtime.close()


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and avoid persistent user data during a smoke test."""
    arguments = build_parser().parse_args(argv)
    if arguments.version:
        if sys.stdout is not None:
            sys.stdout.write(f"{__version__}\n")
        return 0
    base_settings = AppSettings.from_environment().model_copy(
        update={"safe_mode": arguments.safe_mode}
    )
    if arguments.smoke_test:
        with TemporaryDirectory(prefix="pc-manager-agent-smoke-") as temporary_directory:
            settings = base_settings.model_copy(
                update={"data_directory": Path(temporary_directory)}
            )  # 临时目录保存冒烟测试数据库; 上下文结束后自动清除.
            return run_application(settings, smoke_test=True)
    return run_application(base_settings)


def reduce_to_safe_mode(settings: AppSettings) -> AppSettings:
    """Return a reduction-only configuration with no provider, Broker, or capability domain."""
    return settings.model_copy(
        update={
            "safe_mode": True,
            "feature_flags": FeatureFlags(),
            "llm_provider": "disabled",
            "openai_model": None,
            "openai_api_key": None,
            "privileged_broker_mode": "disabled",
            "privileged_broker_path": None,
            "privileged_broker_expected_sha256": None,
            "privileged_broker_trust_mode": "production",
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
