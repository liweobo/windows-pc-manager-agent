"""Select the minimum finite Agent set for an existing request route."""

from __future__ import annotations

from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.task_graph import TaskDomain
from pc_manager_agent.domain.user_requests import RequestDomain, RequestRoute


class AgentSelectionError(RuntimeError):
    """Raised when a route is not eligible for task coordination."""


_ROUTE_DOMAIN: dict[RequestDomain, TaskDomain] = {
    RequestDomain.FILES: TaskDomain.FILE,
    RequestDomain.FILE_OPERATIONS: TaskDomain.FILE,
    RequestDomain.TRASH: TaskDomain.FILE,
    RequestDomain.DIAGNOSTICS: TaskDomain.SYSTEM,
    RequestDomain.PROCESS: TaskDomain.SYSTEM,
    RequestDomain.STARTUP: TaskDomain.SYSTEM,
    RequestDomain.SERVICE: TaskDomain.SYSTEM,
    RequestDomain.SOFTWARE: TaskDomain.SOFTWARE,
    RequestDomain.OFFICE: TaskDomain.OFFICE,
    RequestDomain.BROWSER: TaskDomain.BROWSER,
    RequestDomain.OPTIMIZATION: TaskDomain.OPTIMIZATION,
    RequestDomain.CLEANUP: TaskDomain.OPTIMIZATION,
    RequestDomain.RECYCLE_BIN_EMPTY: TaskDomain.OPTIMIZATION,
}
_DOMAIN_ROLE: dict[TaskDomain, AgentRole] = {
    TaskDomain.FILE: AgentRole.FILE,
    TaskDomain.SYSTEM: AgentRole.SYSTEM,
    TaskDomain.SOFTWARE: AgentRole.SOFTWARE,
    TaskDomain.OFFICE: AgentRole.OFFICE,
    TaskDomain.BROWSER: AgentRole.BROWSER,
    TaskDomain.OPTIMIZATION: AgentRole.OPTIMIZATION,
    TaskDomain.MEMORY: AgentRole.MEMORY_MANAGER,
}


class AgentSelectionPolicy:
    """Avoid starting unrelated Agents for a simple request."""

    def domains_for_route(self, route: RequestRoute) -> tuple[TaskDomain, ...]:
        """Map one already-validated navigation route to one domain."""
        try:
            return (_ROUTE_DOMAIN[route.domain],)
        except KeyError as exc:
            raise AgentSelectionError("Request route is not an actionable task") from exc

    def roles_for_domains(self, domains: tuple[TaskDomain, ...]) -> tuple[AgentRole, ...]:
        """Return Orchestrator, exact domain roles, and one deterministic Verifier."""
        if not domains or len(domains) != len(set(domains)):
            raise AgentSelectionError("Task domains must be non-empty and unique")
        try:
            selected = tuple(_DOMAIN_ROLE[domain] for domain in domains)
        except KeyError as exc:
            raise AgentSelectionError("Task contains an unsupported domain") from exc
        if len(domains) == 1:
            return (AgentRole.ORCHESTRATOR, *selected)
        return (AgentRole.ORCHESTRATOR, AgentRole.PLANNER, *selected, AgentRole.VERIFIER)


def agent_role_for_domain(domain: TaskDomain) -> AgentRole:
    """Return the one domain owner or fail closed."""
    try:
        return _DOMAIN_ROLE[domain]
    except KeyError as exc:
        raise AgentSelectionError("Domain has no Agent owner") from exc
