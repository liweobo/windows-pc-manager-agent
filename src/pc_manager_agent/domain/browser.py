"""Provider-neutral, finite Stage 5C browser domain models."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class BrowserActionKind(StrEnum):
    """Closed action vocabulary; it intentionally contains no generic click or script."""

    OPEN_SESSION = "OPEN_SESSION"
    NAVIGATE = "NAVIGATE"
    BACK = "BACK"
    FORWARD = "FORWARD"
    RELOAD = "RELOAD"
    OBSERVE = "OBSERVE"
    OPEN_LINK = "OPEN_LINK"
    SEARCH = "SEARCH"
    FILTER = "FILTER"
    NEXT_PAGE = "NEXT_PAGE"
    PREVIOUS_PAGE = "PREVIOUS_PAGE"
    EXPAND = "EXPAND"
    PREPARE_DOWNLOAD = "PREPARE_DOWNLOAD"
    DOWNLOAD_DOCUMENT = "DOWNLOAD_DOCUMENT"
    BEGIN_USER_TAKEOVER = "BEGIN_USER_TAKEOVER"
    END_USER_TAKEOVER = "END_USER_TAKEOVER"
    CLOSE_SESSION = "CLOSE_SESSION"


class BrowserDecision(StrEnum):
    """Deterministic policy outcomes."""

    ALLOW = "ALLOW"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"
    USER_TAKEOVER = "USER_TAKEOVER"
    BLOCK = "BLOCK"


class BrowserSessionState(StrEnum):
    """Lifecycle states for one non-resumable ephemeral context."""

    NEW = "NEW"
    ACTIVE = "ACTIVE"
    USER_TAKEOVER = "USER_TAKEOVER"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    CLOSED = "CLOSED"


class BrowserElementRole(StrEnum):
    """Accessibility roles that the worker may expose as actionable references."""

    LINK = "link"
    BUTTON = "button"
    HEADING = "heading"
    TEXTBOX = "textbox"
    SEARCHBOX = "searchbox"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    COMBOBOX = "combobox"
    TABLE = "table"
    ROW = "row"
    CELL = "cell"


class WebContentTrust(StrEnum):
    """All remote page content has the same non-authoritative trust level."""

    UNTRUSTED_WEB_CONTENT = "UNTRUSTED_WEB_CONTENT"


class PromptInjectionSignal(StrEnum):
    """Advisory signals only; deterministic policy remains the authority boundary."""

    OVERRIDE_INSTRUCTIONS = "OVERRIDE_INSTRUCTIONS"
    # Detection signal label only; it is never a credential.
    SECRET_REQUEST = "SECRET_REQUEST"  # nosec B105
    TOOL_INSTRUCTION = "TOOL_INSTRUCTION"
    AUTHORITY_CLAIM = "AUTHORITY_CLAIM"
    DATA_EXFILTRATION = "DATA_EXFILTRATION"


class BrowserElementReference(FrozenModel):
    """Session-local semantic element reference invalidated by every navigation."""

    element_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    page_id: UUID
    navigation_id: UUID
    role: BrowserElementRole
    accessible_name: str = Field(min_length=1, max_length=500)
    href: str | None = Field(default=None, max_length=4_096)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def verify_fingerprint(self) -> Self:
        """Reject a reference whose semantic fields were modified after issuance."""
        payload = json.dumps(
            {"role": self.role.value, "name": self.accessible_name, "href": self.href},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if self.fingerprint != expected:
            raise ValueError("Browser element fingerprint mismatch")
        return self

    @classmethod
    def create(
        cls,
        *,
        session_id: UUID,
        page_id: UUID,
        navigation_id: UUID,
        role: BrowserElementRole,
        accessible_name: str,
        href: str | None = None,
    ) -> BrowserElementReference:
        """Build a reference whose fingerprint binds semantic identity, never a selector."""
        payload = json.dumps(
            {"role": role.value, "name": accessible_name, "href": href},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls(
            session_id=session_id,
            page_id=page_id,
            navigation_id=navigation_id,
            role=role,
            accessible_name=accessible_name,
            href=href,
            fingerprint=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )


class BrowserObservation(FrozenModel):
    """Bounded visible/accessibility-derived page observation."""

    session_id: UUID
    page_id: UUID
    navigation_id: UUID
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    url: str = Field(min_length=1, max_length=4_096)
    title: str = Field(default="", max_length=1_000)
    visible_text: str = Field(default="", max_length=40_000)
    elements: tuple[BrowserElementReference, ...] = Field(default=(), max_length=5_000)
    prompt_injection_signals: tuple[PromptInjectionSignal, ...] = ()
    trust: WebContentTrust = WebContentTrust.UNTRUSTED_WEB_CONTENT
    truncated: bool = False

    @model_validator(mode="after")
    def bind_elements(self) -> Self:
        """Reject references copied from another session or an older page generation."""
        if any(
            element.session_id != self.session_id
            or element.page_id != self.page_id
            or element.navigation_id != self.navigation_id
            for element in self.elements
        ):
            raise ValueError("Observation contains an element from another page generation")
        return self


class BrowserActionRequest(FrozenModel):
    """One typed browser action; unused fields are rejected by cross-field validation."""

    action_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    page_id: UUID | None = None
    navigation_id: UUID | None = None
    kind: BrowserActionKind
    url: str | None = Field(default=None, max_length=4_096)
    element: BrowserElementReference | None = None
    text: str | None = Field(default=None, max_length=1_000)
    expected_origin: str | None = Field(default=None, max_length=512)
    allow_insecure_http: bool = False

    @model_validator(mode="after")
    def enforce_shape(self) -> Self:
        """Make every action form explicit and prevent hidden generic arguments."""
        url_actions = {BrowserActionKind.NAVIGATE}
        element_actions = {
            BrowserActionKind.OPEN_LINK,
            BrowserActionKind.SEARCH,
            BrowserActionKind.FILTER,
            BrowserActionKind.NEXT_PAGE,
            BrowserActionKind.PREVIOUS_PAGE,
            BrowserActionKind.EXPAND,
            BrowserActionKind.PREPARE_DOWNLOAD,
            BrowserActionKind.DOWNLOAD_DOCUMENT,
        }
        text_actions = {BrowserActionKind.SEARCH, BrowserActionKind.FILTER}
        if (self.kind in url_actions) != (self.url is not None):
            raise ValueError("Only navigation actions carry one explicit URL")
        if self.allow_insecure_http and self.kind is not BrowserActionKind.NAVIGATE:
            raise ValueError("Only a confirmed explicit navigation may allow insecure HTTP")
        if (self.kind in element_actions) != (self.element is not None):
            raise ValueError("This action requires exactly one semantic element reference")
        if (self.kind in text_actions) != (self.text is not None):
            raise ValueError("Only search and filter actions carry user text")
        if self.element is not None and self.element.session_id != self.session_id:
            raise ValueError("Element reference belongs to another browser session")
        if self.element is not None and (
            self.page_id != self.element.page_id or self.navigation_id != self.element.navigation_id
        ):
            raise ValueError("Element reference is stale for this page generation")
        return self

    def canonical_digest(self) -> str:
        """Return an exact digest for plan and confirmation binding."""
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class BrowserPolicyResult(FrozenModel):
    """Explain one deterministic decision without carrying executable authority."""

    decision: BrowserDecision
    reason_code: str = Field(pattern=r"^[A-Z0-9_]+$")
    risk_level: RiskLevel
    rollback_level: RollbackLevel
    requires_plan_confirmation: bool
    requires_runtime_confirmation: bool


class BrowserActionResult(FrozenModel):
    """Verified browser result; navigation success is not inferred from a click return."""

    action_id: UUID
    session_id: UUID
    completed: bool
    reason_code: str = Field(pattern=r"^[A-Z0-9_]+$")
    observation: BrowserObservation | None = None


class BrowserSessionDescriptor(FrozenModel):
    """Non-secret identity of an ephemeral browser context."""

    session_id: UUID = Field(default_factory=uuid4)
    state: BrowserSessionState = BrowserSessionState.NEW
    headless: bool = False
    browser_name: str = "chromium"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    profile_persistent: bool = False
    cookies_imported: bool = False


class BrowserModelSummaryRequest(FrozenModel):
    """Minimized page material that may be sent only after external-data consent."""

    origin: str = Field(min_length=1, max_length=512)
    title: str = Field(default="", max_length=1_000)
    visible_sections: tuple[str, ...] = Field(max_length=50)
    trust: WebContentTrust = WebContentTrust.UNTRUSTED_WEB_CONTENT


class BrowserModelSummary(FrozenModel):
    """Advisory summary; it cannot contain action or approval fields."""

    summary: str = Field(min_length=1, max_length=8_000)
    caveats: tuple[str, ...] = Field(default=(), max_length=20)
