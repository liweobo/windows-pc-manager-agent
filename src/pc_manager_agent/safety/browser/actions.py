"""Closed browser action policy independent of Playwright and model output."""

from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlsplit

from pc_manager_agent.domain.browser import (
    BrowserActionKind,
    BrowserActionRequest,
    BrowserDecision,
    BrowserElementRole,
    BrowserPolicyResult,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class BrowserActionPolicy:
    """Classify finite actions and permanently block remote mutations and credentials."""

    _read_actions: ClassVar[frozenset[BrowserActionKind]] = frozenset(
        {
            BrowserActionKind.BACK,
            BrowserActionKind.FORWARD,
            BrowserActionKind.RELOAD,
            BrowserActionKind.OBSERVE,
        }
    )
    _exact_confirmation_actions: ClassVar[frozenset[BrowserActionKind]] = frozenset(
        {
            BrowserActionKind.NAVIGATE,
            BrowserActionKind.OPEN_LINK,
            BrowserActionKind.SEARCH,
            BrowserActionKind.FILTER,
            BrowserActionKind.NEXT_PAGE,
            BrowserActionKind.PREVIOUS_PAGE,
            BrowserActionKind.EXPAND,
        }
    )
    _remote_write_markers = (
        "buy",
        "purchase",
        "checkout",
        "pay",
        "book",
        "reserve",
        "send",
        "post",
        "comment",
        "create account",
        "delete account",
        "accept terms",
        "购买",
        "付款",
        "支付",
        "结账",
        "下单",
        "预订",
        "预约",
        "发送",
        "发布",
        "评论",
        "创建账户",
        "删除账户",
        "同意条款",
    )

    def review(
        self,
        action: BrowserActionRequest,
        *,
        current_origin: str | None,
        authenticated_page: bool = False,
    ) -> BrowserPolicyResult:
        """Return one non-overridable deterministic policy result."""
        if action.kind in {
            BrowserActionKind.BEGIN_USER_TAKEOVER,
            BrowserActionKind.END_USER_TAKEOVER,
        }:
            return BrowserPolicyResult(
                decision=BrowserDecision.USER_TAKEOVER,
                reason_code="BROWSER_MANUAL_AUTH_OR_CAPTCHA_ONLY",
                risk_level=RiskLevel.R0,
                rollback_level=RollbackLevel.NONE,
                requires_plan_confirmation=False,
                requires_runtime_confirmation=False,
            )
        if action.kind in {BrowserActionKind.PREPARE_DOWNLOAD, BrowserActionKind.DOWNLOAD_DOCUMENT}:
            return BrowserPolicyResult(
                decision=BrowserDecision.REQUIRE_CONFIRMATION,
                reason_code="BROWSER_EXACT_DOCUMENT_DOWNLOAD_REVIEW_REQUIRED",
                risk_level=RiskLevel.R1,
                rollback_level=RollbackLevel.FULL,
                requires_plan_confirmation=True,
                requires_runtime_confirmation=False,
            )
        if action.kind in self._read_actions:
            return _r0_allow("BROWSER_CONFIRMED_READ_ACTION")
        if action.kind is BrowserActionKind.OPEN_LINK and action.element is not None:
            if action.element.role is not BrowserElementRole.LINK or action.element.href is None:
                return _block("BROWSER_LINK_SEMANTIC_IDENTITY_REQUIRED")
            semantic_target = f"{action.element.accessible_name} {action.element.href}".casefold()
            if any(marker in semantic_target for marker in self._remote_write_markers):
                return _block("BROWSER_REMOTE_MUTATION_LINK_BLOCKED")
        if action.kind in {BrowserActionKind.SEARCH, BrowserActionKind.FILTER}:
            if action.element is None or action.element.role not in {
                BrowserElementRole.SEARCHBOX,
                BrowserElementRole.TEXTBOX,
                BrowserElementRole.COMBOBOX,
            }:
                return _block("BROWSER_SAFE_FORM_CONTROL_REQUIRED")
            if action.text is None or not action.text.strip():
                return _block("BROWSER_SEARCH_OR_FILTER_TEXT_REQUIRED")
        if action.kind in {
            BrowserActionKind.NEXT_PAGE,
            BrowserActionKind.PREVIOUS_PAGE,
            BrowserActionKind.EXPAND,
        }:
            if action.element is None or action.element.role not in {
                BrowserElementRole.LINK,
                BrowserElementRole.BUTTON,
            }:
                return _block("BROWSER_PAGINATION_OR_EXPAND_CONTROL_REQUIRED")
            name = action.element.accessible_name.casefold()
            allowed_labels = {
                BrowserActionKind.NEXT_PAGE: ("next", "下一页", "下页", ">"),
                BrowserActionKind.PREVIOUS_PAGE: ("previous", "prev", "上一页", "上页", "<"),
                BrowserActionKind.EXPAND: (
                    "more",
                    "details",
                    "expand",
                    "show",
                    "更多",
                    "详情",
                    "展开",
                    "显示",
                ),
            }[action.kind]
            if not any(marker in name for marker in allowed_labels):
                return _block("BROWSER_CONTROL_PURPOSE_NOT_PROVEN_SAFE")
            if any(marker in name for marker in self._remote_write_markers):
                return _block("BROWSER_REMOTE_MUTATION_CONTROL_BLOCKED")
        if action.kind in self._exact_confirmation_actions:
            reason = "BROWSER_EXACT_ACTION_PLAN_REQUIRED"
            target_origin = _action_origin(action)
            if (
                target_origin is not None
                and current_origin is not None
                and target_origin.casefold() != current_origin.casefold()
            ):
                reason = "BROWSER_CROSS_ORIGIN_CONFIRMATION_REQUIRED"
            if authenticated_page:
                reason = "BROWSER_AUTHENTICATED_PAGE_EXACT_CONFIRMATION_REQUIRED"
            return BrowserPolicyResult(
                decision=BrowserDecision.REQUIRE_CONFIRMATION,
                reason_code=reason,
                risk_level=RiskLevel.R0,
                rollback_level=RollbackLevel.NONE,
                requires_plan_confirmation=True,
                requires_runtime_confirmation=False,
            )
        if action.kind in {
            BrowserActionKind.OPEN_SESSION,
            BrowserActionKind.CLOSE_SESSION,
        }:
            return _r0_allow("BROWSER_SESSION_LIFECYCLE")
        return _block("BROWSER_ACTION_NOT_ALLOWLISTED")


def _action_origin(action: BrowserActionRequest) -> str | None:
    value = action.url or (action.element.href if action.element is not None else None)
    if value is None:
        return action.expected_origin
    parts = urlsplit(value)
    if not parts.scheme or not parts.hostname:
        return None
    port = parts.port
    default = (parts.scheme == "https" and port in {None, 443}) or (
        parts.scheme == "http" and port in {None, 80}
    )
    return (
        f"{parts.scheme}://{parts.hostname}"
        if default
        else f"{parts.scheme}://{parts.hostname}:{port}"
    )


def _r0_allow(reason: str) -> BrowserPolicyResult:
    return BrowserPolicyResult(
        decision=BrowserDecision.ALLOW,
        reason_code=reason,
        risk_level=RiskLevel.R0,
        rollback_level=RollbackLevel.NONE,
        requires_plan_confirmation=False,
        requires_runtime_confirmation=False,
    )


def _block(reason: str) -> BrowserPolicyResult:
    return BrowserPolicyResult(
        decision=BrowserDecision.BLOCK,
        reason_code=reason,
        risk_level=RiskLevel.R4,
        rollback_level=RollbackLevel.NONE,
        requires_plan_confirmation=False,
        requires_runtime_confirmation=False,
    )
