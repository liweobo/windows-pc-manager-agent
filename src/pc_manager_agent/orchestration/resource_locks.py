"""In-process task scheduling locks; domain Fresh checks remain authoritative."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from uuid import UUID, uuid4

from pc_manager_agent.domain.task_graph import ResourceAccess, ResourceIdentity


class TaskResourceConflictError(RuntimeError):
    """Raised when concurrent task work overlaps a protected resource."""


@dataclass(frozen=True, slots=True)
class TaskResourceLease:
    """One process-local scheduling lease, not an OS handle or authorization."""

    lease_id: UUID
    task_id: UUID
    node_id: UUID
    resources: tuple[ResourceIdentity, ...]


class TaskResourceLockService:
    """Allow shared reads and serialize every overlapping write intent."""

    def __init__(self) -> None:
        self._leases: dict[UUID, TaskResourceLease] = {}
        self._lock = threading.RLock()

    def acquire(
        self,
        task_id: UUID,
        node_id: UUID,
        resources: tuple[ResourceIdentity, ...],
    ) -> TaskResourceLease:
        """Atomically acquire a sorted unique set or fail without partial ownership."""
        identities = [resource.identity_digest for resource in resources]
        if len(identities) != len(set(identities)):
            raise TaskResourceConflictError("Duplicate resources in one lease")
        normalized = tuple(sorted(resources, key=lambda item: item.identity_digest))
        with self._lock:
            for existing in self._leases.values():
                for requested in normalized:
                    for held in existing.resources:
                        if requested.identity_digest != held.identity_digest:
                            continue
                        if (
                            requested.access is ResourceAccess.WRITE
                            or held.access is ResourceAccess.WRITE
                        ):
                            raise TaskResourceConflictError("Task resource is already in use")
            lease = TaskResourceLease(uuid4(), task_id, node_id, normalized)
            self._leases[lease.lease_id] = lease
            return lease

    def release(self, lease: TaskResourceLease) -> None:
        """Release only the exact lease object originally issued."""
        with self._lock:
            current = self._leases.get(lease.lease_id)
            if current != lease:
                raise TaskResourceConflictError("Unknown or changed resource lease")
            del self._leases[lease.lease_id]

    def release_task(self, task_id: UUID) -> None:
        """Release scheduling leases for cancelled future work only."""
        with self._lock:
            for lease_id in tuple(self._leases):
                if self._leases[lease_id].task_id == task_id:
                    del self._leases[lease_id]
