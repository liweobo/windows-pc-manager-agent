"""Thin voice-to-normal-request handoff; no voice-specific system executor exists."""

from uuid import UUID

from pc_manager_agent.domain.user_requests import RequestRoute, UserRequest
from pc_manager_agent.orchestration.user_requests import UserRequestDispatcher
from pc_manager_agent.voice.session import VoiceSessionCoordinator


class VoiceIntentRouter:
    """Consume a human-reviewed final transcript once, then use the same dispatcher as text."""

    def __init__(
        self, coordinator: VoiceSessionCoordinator, dispatcher: UserRequestDispatcher
    ) -> None:
        self._coordinator, self._dispatcher = coordinator, dispatcher

    def submit(self, reference: UUID, text: str) -> tuple[UserRequest, RequestRoute]:
        """Prepare one channel-neutral handoff; domain selection/confirmation still lies ahead."""
        request = self._coordinator.consume_final(reference, text)
        route = self._dispatcher.route(request)
        self._coordinator.routed(reference)
        return request, route
