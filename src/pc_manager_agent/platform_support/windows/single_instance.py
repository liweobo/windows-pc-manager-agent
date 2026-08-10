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
    # 将方法转换为只读的属性
    def server(self) -> QLocalServer:
        """Expose the server signal so the UI may react to a second launch."""
        return self._server

    def acquire(self) -> bool:
        """检查旧服务端; 存在时返回 False, 否则注册并监听本地端点."""
        probe = QLocalSocket()
        probe.connectToServer(self._server_name)
        if probe.waitForConnected(150):
            probe.disconnectFromServer()
            return False
        probe.abort()  # 强制重置探针状态---客户端
        QLocalServer.removeServer(
            self._server_name
        )  # 移除已确认失效的本地服务注册信息 (或物理管道文件).
        self._acquired = self._server.listen(self._server_name)  # 开启监听----服务端
        return self._acquired

    def close(self) -> None:
        """Close and remove this process's local endpoint."""
        if not self._acquired:
            return
        self._server.close()
        QLocalServer.removeServer(self._server_name)
        self._acquired = False
