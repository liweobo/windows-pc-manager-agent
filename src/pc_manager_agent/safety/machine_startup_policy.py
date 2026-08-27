"""Independent default-deny policy for Stage 4X3 machine HKLM Run actions."""

from __future__ import annotations

import os
from pathlib import Path

from pc_manager_agent.domain.startup_actions import (
    StartupActionType,
    StartupEntryStatus,
    StartupErrorCode,
    StartupManagementMode,
    StartupObservation,
    StartupSafetyAssessment,
    StartupSafetyClass,
    StartupSafetyDecision,
    StartupSource,
)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_PROTECTED_MARKERS = (
    "antivirus",
    "anti-virus",
    "defender",
    "endpoint",
    "firewall",
    "security",
    "driver",
    "firmware",
    "vpn",
    "network",
    "intune",
    "enterprise",
    "group policy",
)
_AGENT_MARKERS = ("windowspcmanageragent", "windows-pc-manager-agent", "pc_manager_agent")


class MachineStartupSafetyPolicy:
    """Allow only one ordinary third-party HKLM Run value in an explicit registry view."""

    def __init__(
        self,
        *,
        agent_root: Path,
        windows_directory: Path | None = None,
    ) -> None:
        self._agent_root = _canonical(agent_root)
        self._windows_directory = _canonical(
            windows_directory or Path(os.environ.get("WINDIR", r"C:\Windows"))
        )

    def assess(
        self,
        observation: StartupObservation,
        action: StartupActionType,
    ) -> StartupSafetyAssessment:
        """Classify one machine value without weakening the Stage 4B current-user policy."""
        reasons: list[StartupErrorCode] = []
        detail = observation.identity.registry
        path = _canonical(observation.executable_path) if observation.executable_path else None
        text = " ".join(
            (
                observation.display_name,
                observation.publisher or "",
                str(path or ""),
            )
        ).casefold()
        safety_class = StartupSafetyClass.UNKNOWN
        if (
            observation.identity.source is not StartupSource.HKLM_RUN
            or observation.scope != "ALL_USERS"
            or detail is None
            or detail.hive != "HKLM"
            or detail.key_path.casefold() != _RUN_KEY.casefold()
            or detail.registry_view not in {"32", "64"}
        ):
            reasons.append(StartupErrorCode.UNSUPPORTED_SOURCE)
        if any(marker in text for marker in _AGENT_MARKERS) or (
            path is not None and _is_within(path, self._agent_root)
        ):
            safety_class = StartupSafetyClass.AGENT_COMPONENT
            reasons.append(StartupErrorCode.BLOCKED_AGENT_COMPONENT)
        elif path is None or not path.is_absolute() or not path.exists() or not path.is_file():
            reasons.append(StartupErrorCode.UNKNOWN_EXECUTABLE)
        elif _is_within(path, self._windows_directory):
            safety_class = StartupSafetyClass.SYSTEM_COMPONENT
            reasons.append(StartupErrorCode.BLOCKED_SYSTEM_COMPONENT)
        elif any(marker in text for marker in _PROTECTED_MARKERS):
            safety_class = StartupSafetyClass.SECURITY_SOFTWARE
            reasons.append(StartupErrorCode.BLOCKED_SECURITY_SOFTWARE)
        elif observation.publisher is None:
            reasons.append(StartupErrorCode.UNKNOWN_PUBLISHER)
        elif any(marker in observation.publisher.casefold() for marker in ("microsoft", "windows")):
            safety_class = StartupSafetyClass.USER_MICROSOFT
            reasons.append(StartupErrorCode.BLOCKED_SYSTEM_COMPONENT)
        else:
            safety_class = StartupSafetyClass.USER_THIRD_PARTY
        if (
            action is StartupActionType.DISABLE
            and observation.status is not StartupEntryStatus.ENABLED
        ):
            reasons.append(StartupErrorCode.ALREADY_DISABLED)
        if (
            action is StartupActionType.RESTORE
            and observation.status is not StartupEntryStatus.ENABLED
        ):
            # Restore classifies the immutable pre-disable observation; the live value must
            # remain absent and is checked separately by the handler.
            reasons.append(StartupErrorCode.ALREADY_ENABLED)
        decision = StartupSafetyDecision.BLOCK if reasons else StartupSafetyDecision.ALLOW
        return StartupSafetyAssessment(
            identity_digest=observation.identity.canonical_digest(),
            safety_class=safety_class,
            decision=decision,
            management_mode=(
                StartupManagementMode.DISABLE_SUPPORTED
                if action is StartupActionType.DISABLE
                else StartupManagementMode.RESTORE_SUPPORTED
            ),
            reason_codes=tuple(dict.fromkeys(reasons)),
            explanation=(
                "Exact ordinary third-party HKLM Run entry is eligible for Stage 4X3"
                if decision is StartupSafetyDecision.ALLOW
                else "Machine startup policy blocked the exact entry"
            ),
        )


def _canonical(path: Path) -> Path:
    return Path(os.path.normcase(str(path.resolve(strict=False))))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
