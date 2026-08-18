"""Deterministic local target resolution for Windows services."""

from pc_manager_agent.domain.service_actions import ServiceErrorCode, ServiceObservation
from pc_manager_agent.domain.service_errors import ServiceActionError
from pc_manager_agent.platform_support.service_control import ServiceControlPlatform


class ServiceTargetResolver:
    """Resolve exactly one service; display names never become execution identity."""

    def __init__(self, platform: ServiceControlPlatform, *, max_items: int = 5_000) -> None:
        if max_items < 1:
            raise ValueError("Service inventory limit must be positive")
        self._platform = platform
        self._max_items = max_items

    def list_current(self) -> tuple[ServiceObservation, ...]:
        """Return a fresh bounded local inventory."""
        return self._platform.list_services(self._max_items)

    def resolve_query(self, query: str) -> ServiceObservation:
        """Resolve one exact service name first, then a unique exact display name."""
        normalized = query.strip().casefold()
        if not normalized:
            raise ServiceActionError(
                ServiceErrorCode.TARGET_QUERY_INVALID,
                "Choose exactly one service",
            )
        inventory = self.list_current()
        by_name = tuple(
            item for item in inventory if item.identity.service_name.casefold() == normalized
        )
        if len(by_name) == 1:
            return by_name[0]
        by_display = tuple(item for item in inventory if item.display_name.casefold() == normalized)
        if len(by_display) == 1:
            return by_display[0]
        if len(by_display) > 1:
            raise ServiceActionError(
                ServiceErrorCode.TARGET_AMBIGUOUS,
                "Multiple services share that display name; select a row or use the service name",
            )
        raise ServiceActionError(
            ServiceErrorCode.TARGET_NOT_FOUND,
            "No installed service has that exact service or display name",
        )

    def resolve_name(self, service_name: str) -> ServiceObservation:
        """Re-read an exact SCM service name selected by trusted local code."""
        observation = self._platform.inspect(service_name)
        if observation is None:
            raise ServiceActionError(
                ServiceErrorCode.TARGET_NOT_FOUND,
                "The selected service no longer exists",
            )
        if observation.identity.service_name.casefold() != service_name.casefold():
            raise ServiceActionError(
                ServiceErrorCode.SERVICE_CONFIGURATION_CHANGED,
                "SCM returned a different service identity",
            )
        return observation
