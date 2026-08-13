"""Deterministic service dependency and dependent analysis."""

from pc_manager_agent.domain.service_actions import (
    ServiceActionType,
    ServiceDependencyAssessment,
    ServiceObservation,
    ServiceState,
)


class ServiceDependencyAnalyzer:
    """Block unsafe dependency conditions without recursive or cascade control."""

    def assess(
        self,
        observation: ServiceObservation,
        action: ServiceActionType,
    ) -> ServiceDependencyAssessment:
        """Return blockers for one exact start, stop, or restart action."""
        blocking_dependencies = (
            tuple(
                item for item in observation.dependencies if item.state is not ServiceState.RUNNING
            )
            if action in {ServiceActionType.START, ServiceActionType.RESTART}
            else ()
        )
        blocking_dependents = (
            tuple(item for item in observation.dependents if item.state is not ServiceState.STOPPED)
            if action in {ServiceActionType.STOP, ServiceActionType.RESTART}
            else ()
        )
        allowed = not blocking_dependencies and not blocking_dependents
        if blocking_dependencies:
            explanation = (
                "Required dependencies are not running; they will not be started automatically"
            )
        elif blocking_dependents:
            explanation = (
                "Running dependent services block stop/restart; cascade stop is prohibited"
            )
        else:
            explanation = "No dependency condition blocks the exact action"
        return ServiceDependencyAssessment(
            allowed=allowed,
            blocking_dependencies=blocking_dependencies,
            blocking_dependents=blocking_dependents,
            graph_digest=observation.dependency_digest(),
            explanation=explanation,
        )
