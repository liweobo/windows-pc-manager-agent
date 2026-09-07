"""Application command-line and GUI entry point."""

from __future__ import annotations

import argparse
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
    ProductionConfigValidator,
    ProductionRuntimeContext,
)
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.platform_support.windows.msi_uninstall import current_process_is_elevated
from pc_manager_agent.platform_support.windows.single_instance import QtSingleInstanceGuard
from pc_manager_agent.ui.main_window import MainWindow
from pc_manager_agent.ui.system_tray import SystemTrayController


def build_parser() -> argparse.ArgumentParser:
    """Build the minimal test-friendly CLI surface."""
    parser = argparse.ArgumentParser(description="Windows PC Manager Agent")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="create the GUI offscreen, initialize dependencies, then exit",
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
                ),
            )
        runtime = ApplicationRuntime(settings)
    except Exception as exc:
        QMessageBox.critical(None, "启动失败", f"安全启动检查或本地数据库初始化失败：{exc}")
        guard.close()
        return 1
    window = MainWindow(runtime)

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


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and avoid persistent user data during a smoke test."""
    arguments = build_parser().parse_args(argv)
    if arguments.smoke_test and not bool(getattr(sys, "frozen", False)):
        with TemporaryDirectory(prefix="pc-manager-agent-smoke-") as temporary_directory:
            settings = AppSettings.from_environment().model_copy(
                update={"data_directory": Path(temporary_directory)}
            )  # 临时目录保存冒烟测试数据库; 上下文结束后自动清除.
            return run_application(settings, smoke_test=True)
    return run_application(AppSettings.from_environment())


if __name__ == "__main__":
    raise SystemExit(main())
