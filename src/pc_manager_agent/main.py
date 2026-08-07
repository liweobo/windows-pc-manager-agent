"""Application command-line and GUI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from pc_manager_agent import __version__
from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.config.settings import AppSettings
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
    existing_application = QApplication.instance()
    application = (
        cast(QApplication, existing_application)
        if existing_application is not None
        else QApplication(sys.argv[:1])
    )
    application.setApplicationName("Windows PC Manager Agent")
    application.setOrganizationName("liweobo")
    application.setQuitOnLastWindowClosed(False)
    guard = QtSingleInstanceGuard()
    if not guard.acquire():
        return 0
    try:
        runtime = ApplicationRuntime(settings)
    except Exception as exc:
        QMessageBox.critical(None, "启动失败", f"无法初始化本地审计数据库：{exc}")
        guard.close()
        return 1
    window = MainWindow(runtime)

    def controlled_quit() -> None:
        window.request_quit()
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
    if arguments.smoke_test:
        with TemporaryDirectory(prefix="pc-manager-agent-smoke-") as temporary_directory:
            settings = AppSettings.from_environment().model_copy(
                update={"data_directory": Path(temporary_directory)}
            )
            return run_application(settings, smoke_test=True)
    return run_application(AppSettings.from_environment())


if __name__ == "__main__":
    raise SystemExit(main())
