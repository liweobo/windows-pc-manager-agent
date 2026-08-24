"""Fresh-evidence validator for approved winget uninstall Previews."""

from __future__ import annotations

from datetime import UTC, datetime

from pc_manager_agent.domain.winget_uninstall import WingetUninstallPlan, WingetUninstallPreview


class WingetUninstallSafetyError(RuntimeError):
    """Raised when runtime evidence no longer matches the approved Preview."""


class WingetUninstallSafetyValidator:
    """Reject stale plans, changed package/software links, or changed executable identity."""

    def validate(
        self,
        plan: WingetUninstallPlan,
        approved: WingetUninstallPreview,
        fresh: WingetUninstallPreview,
    ) -> None:
        """Require exact invariant reproduction immediately before confirmation."""
        now = datetime.now(UTC)
        if not approved.executable or not fresh.executable:
            raise WingetUninstallSafetyError("winget uninstall Preview is blocked")
        if now >= approved.expires_at or now >= fresh.expires_at:
            raise WingetUninstallSafetyError("winget uninstall Preview expired")
        if approved.plan_digest != plan.canonical_digest():
            raise WingetUninstallSafetyError("winget uninstall plan changed")
        if approved.invariant_digest() != fresh.invariant_digest():
            raise WingetUninstallSafetyError("winget package, software, or safety evidence changed")

    def validate_invariant(
        self,
        plan: WingetUninstallPlan,
        approved_invariant_digest: str,
        fresh: WingetUninstallPreview,
    ) -> None:
        """Validate fresh evidence against the digest persisted with the first gate."""
        if not fresh.executable or datetime.now(UTC) >= fresh.expires_at:
            raise WingetUninstallSafetyError("fresh winget Preview is blocked or expired")
        if fresh.plan_digest != plan.canonical_digest():
            raise WingetUninstallSafetyError("winget plan changed")
        if fresh.invariant_digest() != approved_invariant_digest:
            raise WingetUninstallSafetyError("winget runtime invariant changed")
