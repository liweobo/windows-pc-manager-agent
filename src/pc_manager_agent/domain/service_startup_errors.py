"""Typed failures for narrow service startup configuration workflows."""

from pc_manager_agent.domain.service_startup_actions import ServiceStartupErrorCode


class ServiceStartupActionError(RuntimeError):
    """Expose one stable error code without leaking raw SCM configuration details."""

    def __init__(self, code: ServiceStartupErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
