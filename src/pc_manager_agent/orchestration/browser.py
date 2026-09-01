"""Deterministic Stage 5C browser planning and execution closure."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

from pc_manager_agent.audit.browser import BrowserAuditLogger
from pc_manager_agent.browser.adapter import BrowserAdapter
from pc_manager_agent.config.browser import BrowserSecuritySettings
from pc_manager_agent.confirmation.browser import BrowserConfirmationService
from pc_manager_agent.domain.browser import (
    BrowserActionKind,
    BrowserActionRequest,
    BrowserActionResult,
    BrowserDecision,
    BrowserElementReference,
    BrowserObservation,
    BrowserSessionDescriptor,
)
from pc_manager_agent.domain.browser_downloads import (
    BrowserDownloadIdentity,
    BrowserDownloadPreview,
    BrowserDownloadRecoveryRecord,
    BrowserWorkerDownload,
)
from pc_manager_agent.domain.browser_plans import BrowserConfirmationRecord, BrowserTaskPlan
from pc_manager_agent.persistence.browser import (
    BrowserConfirmationRepository,
    BrowserWriteExecutionGuard,
)
from pc_manager_agent.safety.browser.actions import BrowserActionPolicy
from pc_manager_agent.safety.browser.downloads import (
    BrowserDownloadManager,
    BrowserDownloadPolicy,
    BrowserDownloadPolicyError,
)
from pc_manager_agent.safety.browser.network import (
    BrowserNetworkPolicyError,
    BrowserUrlPolicy,
)
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


class BrowserWorkflowError(RuntimeError):
    """Raised when a browser plan, confirmation, page generation, or result fails closed."""


class BrowserPlanCompiler:
    """Compile one exact action after URL and action policy review."""

    def __init__(
        self,
        url_policy: BrowserUrlPolicy,
        action_policy: BrowserActionPolicy,
    ) -> None:
        self._url_policy = url_policy
        self._action_policy = action_policy

    def navigation(
        self,
        session: BrowserSessionDescriptor,
        url: str,
        *,
        user_goal_summary: str,
    ) -> BrowserTaskPlan:
        """Compile explicit HTTPS or separately acknowledged HTTP navigation."""
        allow_http = False
        try:
            validated = self._url_policy.validate(url)
        except BrowserNetworkPolicyError as exc:
            if str(exc) != "BROWSER_HTTP_REQUIRES_CONFIRMATION":
                raise
            validated = self._url_policy.validate(url, allow_http=True)
            allow_http = True
        action = BrowserActionRequest(
            session_id=session.session_id,
            kind=BrowserActionKind.NAVIGATE,
            url=validated.normalized_url,
            expected_origin=validated.origin,
            allow_insecure_http=allow_http,
        )
        policy = self._action_policy.review(action, current_origin=None)
        if policy.decision is BrowserDecision.BLOCK:
            raise BrowserWorkflowError(policy.reason_code)
        reason = "打开明确网址并生成只读页面观察"
        if allow_http:
            reason += "；连接未加密，必须明确确认"
        return BrowserTaskPlan(
            summary="受控网页导航",
            user_goal_summary=user_goal_summary[:500],
            action=action,
            policy=policy,
            allowed_origin=validated.origin,
            expected_effect=reason,
        )

    def semantic_action(
        self,
        observation: BrowserObservation,
        *,
        element: BrowserElementReference | None,
        kind: BrowserActionKind,
        text: str | None = None,
        user_goal_summary: str,
        authenticated_page: bool = False,
    ) -> BrowserTaskPlan:
        """Compile one action bound to the current page generation and semantic reference."""
        if element is not None and element not in observation.elements:
            raise BrowserWorkflowError("BROWSER_ELEMENT_NOT_IN_CURRENT_OBSERVATION")
        action = BrowserActionRequest(
            session_id=observation.session_id,
            page_id=observation.page_id,
            navigation_id=observation.navigation_id,
            kind=kind,
            element=element,
            text=text,
            expected_origin=_origin_from_url(observation.url),
        )
        if element is not None and element.href is not None:
            self._url_policy.validate(element.href)
        current_origin = _origin_from_url(observation.url)
        policy = self._action_policy.review(
            action,
            current_origin=current_origin,
            authenticated_page=authenticated_page,
        )
        if policy.decision is BrowserDecision.BLOCK:
            raise BrowserWorkflowError(policy.reason_code)
        return BrowserTaskPlan(
            summary=f"受控浏览器动作：{kind.value}",
            user_goal_summary=user_goal_summary[:500],
            action=action,
            policy=policy,
            allowed_origin=_target_origin(action, current_origin),
            expected_effect="只执行一个语义动作；执行前重新解析，变化或歧义即停止",
        )


class BrowserTaskService:
    """Own one active session, durable approval consumption, tools, audit, and recovery."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        adapter: BrowserAdapter,
        compiler: BrowserPlanCompiler,
        confirmation: BrowserConfirmationService,
        confirmation_repository: BrowserConfirmationRepository,
        write_guard: BrowserWriteExecutionGuard,
        audit: BrowserAuditLogger,
        temporary_directory: Path,
        settings: BrowserSecuritySettings | None = None,
        url_policy: BrowserUrlPolicy | None = None,
        action_policy: BrowserActionPolicy | None = None,
        download_policy: BrowserDownloadPolicy | None = None,
        download_manager: BrowserDownloadManager | None = None,
    ) -> None:
        self._registry = registry
        self._adapter = adapter
        self._compiler = compiler
        self._confirmation = confirmation
        self._confirmation_repository = confirmation_repository
        self._write_guard = write_guard
        self._audit = audit
        self._temporary_directory = temporary_directory
        self._settings = settings or BrowserSecuritySettings()
        self._url_policy = url_policy or BrowserUrlPolicy()
        self._action_policy = action_policy or BrowserActionPolicy()
        self._download_policy = download_policy or BrowserDownloadPolicy()
        self._download_manager = download_manager or BrowserDownloadManager(self._download_policy)
        self._session: BrowserSessionDescriptor | None = None
        self._observation: BrowserObservation | None = None
        self._cancellation = CancellationToken()

    @property
    def session(self) -> BrowserSessionDescriptor | None:
        """Return non-secret session metadata for the UI."""
        return self._session

    @property
    def observation(self) -> BrowserObservation | None:
        """Return the current bounded observation, if any."""
        return self._observation

    def start(self, *, headless: bool = False) -> BrowserSessionDescriptor:
        """Open one empty ephemeral context through the registered tool."""
        if self._session is not None:
            raise BrowserWorkflowError("BROWSER_SESSION_ALREADY_STARTED")
        result = self._registry.execute(
            "browser.session.open",
            {"headless": headless},
            self._cancellation,
        )
        if not isinstance(result, BrowserSessionDescriptor):
            raise BrowserWorkflowError("BROWSER_SESSION_RESULT_INVALID")
        self._session = result
        return result

    def prepare_navigation(self, url: str, *, user_goal_summary: str) -> BrowserTaskPlan:
        """Build and audit an exact navigation plan without contacting the destination."""
        session = self._require_session()
        plan = self._compiler.navigation(session, url, user_goal_summary=user_goal_summary)
        self._audit.plan_reviewed(plan)
        return plan

    def prepare_action(
        self,
        *,
        element_id: UUID | None,
        kind: BrowserActionKind,
        text: str | None = None,
        user_goal_summary: str,
        authenticated_page: bool = False,
    ) -> BrowserTaskPlan:
        """Resolve a UI-selected local UUID and compile one exact semantic plan."""
        observation = self._require_observation()
        element = None
        if element_id is not None:
            element = next(
                (item for item in observation.elements if item.element_id == element_id),
                None,
            )
            if element is None:
                raise BrowserWorkflowError("BROWSER_ELEMENT_REFERENCE_NOT_FOUND")
        plan = self._compiler.semantic_action(
            observation,
            element=element,
            kind=kind,
            text=text,
            user_goal_summary=user_goal_summary,
            authenticated_page=authenticated_page,
        )
        self._audit.plan_reviewed(plan)
        return plan

    def request_confirmation(self, plan: BrowserTaskPlan) -> BrowserConfirmationRecord:
        """Persist a short-lived exact approval request."""
        return self._confirmation.request(plan)

    def resolve_confirmation(
        self,
        plan: BrowserTaskPlan,
        confirmation_id: UUID,
        *,
        approved: bool,
    ) -> None:
        """Persist and audit an explicit user decision."""
        self._confirmation.resolve(confirmation_id, approved=approved)
        self._audit.confirmation_resolved(plan, approved)

    def execute(self, plan: BrowserTaskPlan, confirmation_id: UUID) -> BrowserActionResult:
        """Consume approval, freshly review, invoke one registered tool, and verify observation."""
        self._require_plan_session(plan)
        fresh_policy = self._action_policy.review(
            plan.action,
            current_origin=_origin_from_url(self._observation.url) if self._observation else None,
        )
        if (
            fresh_policy.decision is BrowserDecision.BLOCK
            or fresh_policy.risk_level != plan.policy.risk_level
        ):
            raise BrowserWorkflowError("BROWSER_POLICY_CHANGED")
        self._fresh_validate_action_url(plan.action)
        self._confirmation.consume(confirmation_id, plan)
        if plan.action.kind is BrowserActionKind.NAVIGATE:
            output = self._registry.execute(
                "browser.page.navigate",
                {
                    "url": plan.action.url,
                    "allow_insecure_http": plan.action.allow_insecure_http,
                },
                self._cancellation,
            )
            if not isinstance(output, BrowserObservation):
                raise BrowserWorkflowError("BROWSER_NAVIGATION_RESULT_INVALID")
            result = BrowserActionResult(
                action_id=plan.action.action_id,
                session_id=plan.action.session_id,
                completed=True,
                reason_code="BROWSER_NAVIGATION_VERIFIED",
                observation=output,
            )
        else:
            self._require_current_generation(plan.action)
            output = self._registry.execute(
                "browser.element.activate",
                {"action": plan.action.model_dump(mode="json")},
                self._cancellation,
            )
            if not isinstance(output, BrowserActionResult):
                raise BrowserWorkflowError("BROWSER_ACTION_RESULT_INVALID")
            result = output
        if result.observation is not None:
            self._set_observation(result.observation)
        self._audit.action_completed(plan, result)
        return result

    def observe(self) -> BrowserObservation:
        """Refresh bounded page state in an already confirmed active session."""
        session = self._require_session()
        output = self._registry.execute(
            "browser.page.observe",
            {"session_id": str(session.session_id)},
            self._cancellation,
        )
        if not isinstance(output, BrowserObservation):
            raise BrowserWorkflowError("BROWSER_OBSERVATION_RESULT_INVALID")
        self._set_observation(output)
        return output

    def begin_user_takeover(self) -> BrowserActionResult:
        """Invalidate Agent authority and let the user handle credentials/MFA/CAPTCHA manually."""
        observation = self._require_observation()
        self._confirmation_repository.invalidate_session(observation.session_id)
        action = BrowserActionRequest(
            session_id=observation.session_id,
            page_id=observation.page_id,
            navigation_id=observation.navigation_id,
            kind=BrowserActionKind.BEGIN_USER_TAKEOVER,
            expected_origin=_origin_from_url(observation.url),
        )
        output = self._registry.execute(
            "browser.element.activate",
            {"action": action.model_dump(mode="json")},
            self._cancellation,
        )
        if not isinstance(output, BrowserActionResult):
            raise BrowserWorkflowError("BROWSER_USER_TAKEOVER_RESULT_INVALID")
        result = output
        self._observation = None
        return result

    def end_user_takeover(self) -> BrowserObservation:
        """Reload, freshly observe, and invalidate all pre-takeover element references."""
        session = self._require_session()
        action = BrowserActionRequest(
            session_id=session.session_id,
            kind=BrowserActionKind.END_USER_TAKEOVER,
        )
        output = self._registry.execute(
            "browser.element.activate",
            {"action": action.model_dump(mode="json")},
            self._cancellation,
        )
        if not isinstance(output, BrowserActionResult):
            raise BrowserWorkflowError("BROWSER_HAND_BACK_RESULT_INVALID")
        result = output
        if result.observation is None:
            raise BrowserWorkflowError("BROWSER_HAND_BACK_REVALIDATION_FAILED")
        self._confirmation_repository.invalidate_session(session.session_id)
        self._set_observation(result.observation)
        return result.observation

    def prepare_download(
        self,
        *,
        element_id: UUID,
        destination_directory: Path,
        user_goal_summary: str,
    ) -> tuple[BrowserDownloadPreview, BrowserTaskPlan]:
        """Build one exact R1 Preview from the current semantic link and local destination."""
        observation = self._require_observation()
        element = next(
            (item for item in observation.elements if item.element_id == element_id),
            None,
        )
        if element is None or element.href is None:
            raise BrowserWorkflowError("BROWSER_DOWNLOAD_LINK_REQUIRED")
        validated = self._url_policy.validate(element.href)
        filename = _download_filename(element.href)
        mime_type = _declared_mime(filename)
        self._download_policy.validate_declared(
            filename,
            mime_type,
            None,
            max_size_bytes=self._settings.max_download_bytes,
        )
        destination_root = destination_directory.resolve(strict=True)
        if not destination_root.is_dir():
            raise BrowserWorkflowError("BROWSER_DOWNLOAD_DESTINATION_NOT_DIRECTORY")
        preview = BrowserDownloadPreview(
            session_id=observation.session_id,
            page_id=observation.page_id,
            navigation_id=observation.navigation_id,
            element_id=element.element_id,
            source_url=validated.normalized_url,
            source_origin=validated.origin,
            suggested_filename=filename,
            declared_mime_type=mime_type,
            destination=destination_root / filename,
            max_size_bytes=self._settings.max_download_bytes,
        )
        action = BrowserActionRequest(
            session_id=observation.session_id,
            page_id=observation.page_id,
            navigation_id=observation.navigation_id,
            kind=BrowserActionKind.DOWNLOAD_DOCUMENT,
            element=element,
            expected_origin=validated.origin,
        )
        policy = self._action_policy.review(
            action, current_origin=_origin_from_url(observation.url)
        )
        plan = BrowserTaskPlan(
            summary="下载一个经过类型和大小验证的文档",
            user_goal_summary=user_goal_summary[:500],
            action=action,
            policy=policy,
            allowed_origin=validated.origin,
            expected_effect=f"保存 {filename}；不覆盖；最大 50 MiB；可条件完整回滚",
            download_preview_digest=preview.canonical_digest(),
        )
        self._audit.plan_reviewed(plan)
        return preview, plan

    def execute_download(
        self,
        preview: BrowserDownloadPreview,
        plan: BrowserTaskPlan,
        confirmation_id: UUID,
    ) -> tuple[BrowserDownloadIdentity, BrowserDownloadRecoveryRecord]:
        """Consume authority, revalidate identity, stage, verify, and commit one document."""
        if plan.download_preview_digest != preview.canonical_digest():
            raise BrowserWorkflowError("BROWSER_DOWNLOAD_PREVIEW_CHANGED")
        self._require_current_generation(plan.action)
        self._url_policy.validate(preview.source_url)
        self._download_policy.validate_declared(
            preview.suggested_filename,
            preview.declared_mime_type,
            preview.declared_size_bytes,
            max_size_bytes=preview.max_size_bytes,
        )
        self._confirmation.consume(confirmation_id, plan)
        self._temporary_directory.mkdir(parents=True, exist_ok=True)
        arguments = {
            "action": plan.action.model_dump(mode="json"),
            "temporary_directory": str(self._temporary_directory),
        }
        authorization = self._write_guard.issue(
            plan_id=plan.plan_id,
            operation_id=plan.action.action_id,
            preview_id=preview.preview_id,
            tool_name="browser.document.download",
            arguments=arguments,
        )
        output = self._registry.execute(
            "browser.document.download",
            arguments,
            self._cancellation,
            authorization,
        )
        if not isinstance(output, BrowserWorkerDownload):
            raise BrowserWorkflowError("BROWSER_DOWNLOAD_WORKER_RESULT_INVALID")
        temporary_root = self._temporary_directory.resolve(strict=True)
        temporary_path = output.temporary_path.resolve(strict=True)
        if temporary_path.parent != temporary_root:
            raise BrowserWorkflowError("BROWSER_DOWNLOAD_TEMP_SCOPE_MISMATCH")
        if output.suggested_filename != preview.suggested_filename:
            self._download_manager.retain_staged(
                output.temporary_path,
                reason="filename-mismatch",
            )
            raise BrowserWorkflowError("BROWSER_DOWNLOAD_FILENAME_CHANGED")
        identity, recovery = self._download_manager.commit(preview, output.temporary_path)
        self._download_manager.retain_staged(
            output.temporary_path,
            reason="validated-staging-copy",
        )
        self._audit.download_completed(preview, identity)
        return identity, recovery

    def rollback_download(self, record: BrowserDownloadRecoveryRecord) -> Path:
        """Move an unchanged download to retained recovery storage without overwriting."""
        path = self._download_manager.rollback(record)
        self._audit.download_rolled_back(record)
        return path

    def invalidate_pending_authority(self) -> int:
        """Invalidate every unconsumed approval when the UI prepares a changed plan."""
        session = self._require_session()
        return self._confirmation_repository.invalidate_session(session.session_id)

    def cancel(self) -> None:
        """Stop future actions, invalidate approvals, and close the disposable context."""
        self._cancellation.cancel()
        if self._session is not None:
            self._confirmation_repository.invalidate_session(self._session.session_id)
        self._adapter.cancel()
        self._observation = None

    def close(self) -> None:
        """Destroy browser state and invalidate every remaining session approval."""
        if self._session is not None:
            self._confirmation_repository.invalidate_session(self._session.session_id)
        self._adapter.close()
        self._observation = None

    def _set_observation(self, observation: BrowserObservation) -> None:
        session = self._require_session()
        if observation.session_id != session.session_id:
            raise BrowserWorkflowError("BROWSER_OBSERVATION_SESSION_MISMATCH")
        self._confirmation_repository.invalidate_session(session.session_id)
        self._observation = observation
        self._audit.observation(observation)

    def _fresh_validate_action_url(self, action: BrowserActionRequest) -> None:
        value = action.url or (action.element.href if action.element else None)
        if value is not None:
            self._url_policy.validate(value, allow_http=action.allow_insecure_http)

    def _require_current_generation(self, action: BrowserActionRequest) -> None:
        observation = self._require_observation()
        if (
            action.session_id != observation.session_id
            or action.page_id != observation.page_id
            or action.navigation_id != observation.navigation_id
        ):
            raise BrowserWorkflowError("BROWSER_ACTION_PAGE_GENERATION_CHANGED")
        if action.element is not None and action.element not in observation.elements:
            raise BrowserWorkflowError("BROWSER_ACTION_ELEMENT_CHANGED")

    def _require_plan_session(self, plan: BrowserTaskPlan) -> None:
        session = self._require_session()
        if plan.action.session_id != session.session_id:
            raise BrowserWorkflowError("BROWSER_PLAN_SESSION_MISMATCH")

    def _require_session(self) -> BrowserSessionDescriptor:
        if self._session is None:
            raise BrowserWorkflowError("BROWSER_SESSION_NOT_STARTED")
        return self._session

    def _require_observation(self) -> BrowserObservation:
        if self._observation is None:
            raise BrowserWorkflowError("BROWSER_PAGE_NOT_OBSERVED")
        return self._observation


def _origin_from_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.hostname is None:
        raise BrowserWorkflowError("BROWSER_CURRENT_ORIGIN_INVALID")
    port = parts.port
    default = (parts.scheme == "https" and port in {None, 443}) or (
        parts.scheme == "http" and port in {None, 80}
    )
    return (
        f"{parts.scheme}://{parts.hostname}"
        if default
        else f"{parts.scheme}://{parts.hostname}:{port}"
    )


def _target_origin(action: BrowserActionRequest, current_origin: str) -> str:
    value = action.url or (action.element.href if action.element else None)
    return _origin_from_url(value) if value else current_origin


def _download_filename(url: str) -> str:
    name = Path(unquote(urlsplit(url).path)).name
    if not name:
        raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_FILENAME_MISSING")
    return name


def _declared_mime(filename: str) -> str:
    suffix = Path(filename).suffix.casefold()
    values = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".csv": "text/csv",
        ".json": "application/json",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    try:
        return values[suffix]
    except KeyError as exc:
        raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_TYPE_BLOCKED") from exc
