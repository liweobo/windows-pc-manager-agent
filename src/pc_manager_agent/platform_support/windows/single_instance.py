"""Windows-compatible single-instance guard using Qt local IPC."""

from __future__ import annotations

from PySide6.QtNetwork import QLocalServer, QLocalSocket


class QtSingleInstanceGuard:
    """Own one local server name for the lifetime of the application."""

    def __init__(self, server_name: str = "WindowsPCManagerAgent-0.1") -> None:
        self._server_name = server_name
        self._server = QLocalServer()
        self._acquired = False

    @property
    def server(self) -> QLocalServer:
        """Expose the server signal so the UI may react to a second launch."""
        return self._server

    def acquire(self) -> bool:
        """Detect an active owner, removing only a demonstrably stale endpoint."""
        probe = QLocalSocket()
        probe.connectToServer(self._server_name)
        if probe.waitForConnected(150):
            probe.disconnectFromServer()
            return False
        probe.abort()
        QLocalServer.removeServer(self._server_name)
        self._acquired = self._server.listen(self._server_name)
        return self._acquired

    def close(self) -> None:
        """Close and remove this process's local endpoint."""
        if not self._acquired:
            return
        self._server.close()
        QLocalServer.removeServer(self._server_name)
        self._acquired = False
