"""System tray presentation without business-logic execution."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QStyle, QSystemTrayIcon


class SystemTrayController(QObject):
    """Expose open/hide/exit actions and delegate controlled shutdown."""

    def __init__(self, window: QMainWindow, quit_callback: Callable[[], None]) -> None:
        super().__init__(window)
        self._window = window
        self._tray = QSystemTrayIcon(window)
        icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self._tray.setIcon(icon)
        self._tray.setToolTip("Windows PC Manager Agent")
        menu = QMenu(window)
        open_action = QAction("打开主窗口", menu)
        hide_action = QAction("隐藏到托盘", menu)
        exit_action = QAction("安全退出", menu)
        open_action.triggered.connect(self.show_window)
        hide_action.triggered.connect(window.hide)
        exit_action.triggered.connect(quit_callback)
        menu.addAction(open_action)
        menu.addAction(hide_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)

    @property
    def is_available(self) -> bool:
        """Return whether the desktop session exposes a system tray."""
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        """Show the icon when a tray is available."""
        if self.is_available:
            self._tray.show()

    def hide(self) -> None:
        """Remove the tray icon."""
        self._tray.hide()

    def show_window(self) -> None:
        """Restore and focus the main window."""
        self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason is QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()
