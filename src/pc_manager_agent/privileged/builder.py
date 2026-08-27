"""Strict construction of short-lived privileged capability requests."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pc_manager_agent.confirmation.privileged_actions import (
    PrivilegedActionConfirmationService,
)
from pc_manager_agent.domain.privileged_actions import (
    PrivilegedActionEnvelope,
    PrivilegedActionPlan,
    PrivilegedActionPreview,
    PrivilegedActionRequest,
    PrivilegedCallerContext,
    PrivilegedExecutionMode,
    PrivilegedPayload,
    PrivilegeResolution,
    canonical_model_digest,
)
from pc_manager_agent.privileged.authentication import PrivilegedRequestAuthenticator
from pc_manager_agent.privileged.serialization import PrivilegedRequestSerializer


class PrivilegedActionBuilder:
    """Translate validated business evidence without adding or changing its semantics."""

    def __init__(
        self,
        serializer: PrivilegedRequestSerializer,
        authenticator: PrivilegedRequestAuthenticator,
        confirmations: PrivilegedActionConfirmationService,
        *,
        request_ttl_seconds: int = 120,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not 15 <= request_ttl_seconds <= 600:
            raise ValueError("Privileged request TTL must be between 15 and 600 seconds")
        self._serializer = serializer
        self._authenticator = authenticator
        self._confirmations = confirmations
        self._ttl = request_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def plan(
        self,
        *,
        source_plan_id: UUID,
        source_plan_hash: str,
        payload: PrivilegedPayload,
        target_identity_hash: str,
        object_summary: str,
    ) -> PrivilegedActionPlan:
        """Build an exact R3 protocol plan from deterministic upstream evidence."""
        return PrivilegedActionPlan(
            source_plan_id=source_plan_id,
            source_plan_hash=source_plan_hash,
            action_type=payload.payload_type,
            payload=payload,
            payload_digest=canonical_model_digest(payload.model_dump(mode="json")),
            target_identity_hash=target_identity_hash,
            object_summary_digest=hashlib.sha256(object_summary.encode("utf-8")).hexdigest(),
        )

    def preview(
        self,
        plan: PrivilegedActionPlan,
        *,
        target_state_hash: str,
        safety_digest: str,
        privilege_resolution: PrivilegeResolution,
        execution_mode: PrivilegedExecutionMode = PrivilegedExecutionMode.MOCK,
    ) -> PrivilegedActionPreview:
        """Build a mode-bound fresh Preview without creating an authorization request."""
        mock_only = execution_mode is PrivilegedExecutionMode.MOCK
        return PrivilegedActionPreview(
            plan_id=plan.plan_id,
            plan_hash=plan.canonical_digest(),
            action_type=plan.action_type,
            target_identity_hash=plan.target_identity_hash,
            target_state_hash=target_state_hash,
            safety_digest=safety_digest,
            risk_level=plan.risk_level,
            privilege_resolution=privilege_resolution,
            execution_mode=execution_mode,
            mock_only=mock_only,
            warning=(
                "Stage 4X1 validates a Mock Broker request only; no real elevated system "
                "operation will be performed."
                if mock_only
                else (
                    "Stage 4X2 may request Windows UAC only after both exact confirmations; "
                    "the one-shot Broker may execute only SERVICE_START or SERVICE_STOP."
                )
            ),
        )

    def build(
        self,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
        *,
        plan_confirmation_id: UUID,
        runtime_confirmation_id: UUID,
        caller: PrivilegedCallerContext,
    ) -> PrivilegedActionEnvelope:
        """Build and authenticate only after both exact confirmations are approved."""
        parent, runtime = self._confirmations.require_approved_pair(
            plan_confirmation_id,
            runtime_confirmation_id,
            plan,
            preview,
        )
        now = self._now()
        nonce = secrets.token_urlsafe(32)
        request = PrivilegedActionRequest(
            action_type=plan.action_type,
            payload=plan.payload,
            payload_digest=plan.payload_digest,
            target_identity_hash=plan.target_identity_hash,
            plan_id=plan.plan_id,
            plan_hash=plan.canonical_digest(),
            preview_id=preview.preview_id,
            preview_hash=preview.canonical_digest(),
            plan_confirmation_id=parent.confirmation_id,
            confirmation_id=runtime.confirmation_id,
            object_summary_digest=plan.object_summary_digest,
            risk_level=plan.risk_level,
            privilege_requirement=plan.privilege_requirement,
            agent_instance_id=caller.agent_instance_id,
            caller_context_reference=caller.context_id,
            nonce=nonce,
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl),
        )
        canonical = self._serializer.canonical_request_bytes(request)
        digest = hashlib.sha256(canonical).hexdigest()
        return PrivilegedActionEnvelope(
            request=request,
            request_digest=digest,
            integrity=self._authenticator.sign(canonical),
        )
