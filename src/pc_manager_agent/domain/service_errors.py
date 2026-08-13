"""Typed failures for controlled service state workflows."""

from pc_manager_agent.domain.service_actions import ServiceErrorCode


class ServiceActionError(RuntimeError):
    """Workflow failure carrying a stable audit/UI error code."""

    def __init__(self, code: ServiceErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class ServiceConfigurationChangedError(ServiceActionError):
    """Raised when configuration identity differs from the approved Preview."""

    def __init__(
        self, message: str = "Service configuration changed; create a new Preview"
    ) -> None:
        super().__init__(ServiceErrorCode.SERVICE_CONFIGURATION_CHANGED, message)


class ServicePermissionError(ServiceActionError):
    """Raised when ordinary-user service access is insufficient."""

    def __init__(self, message: str = "Current ordinary-user permissions are insufficient") -> None:
        super().__init__(ServiceErrorCode.PRIVILEGE_REQUIRED, message)
