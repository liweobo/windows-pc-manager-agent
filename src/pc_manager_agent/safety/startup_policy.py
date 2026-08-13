"""Deterministic default-deny policy for Stage 4B startup actions."""

from __future__ import annotations

import os
from pathlib import Path

from pc_manager_agent.domain.startup_actions import (
    StartupActionType,
    StartupErrorCode,
    StartupManagementMode,
    StartupObservation,
    StartupSafetyAssessment,
    StartupSafetyClass,
    StartupSafetyDecision,
    StartupSource,
)

_MICROSOFT_MARKERS = ("microsoft", "windows")
_SECURITY_MARKERS = (
    "antivirus",
    "anti-virus",
    "defender",
    "endpoint",
    "firewall",
    "security",
    "crowdstrike",
    "sentinelone",
    "carbon black",
    "sophos",
    "mcafee",
    "symantec",
    "kaspersky",
    "bitdefender",
    "malwarebytes",
    "edr",
)
_DRIVER_MARKERS = ("driver", "firmware", "nvidia container", "amd external events")
_ENTERPRISE_MARKERS = ("group policy", "enterprise management", "intune", "workspace one")
_AGENT_MARKERS = ("windowspcmanageragent", "windows-pc-manager-agent", "pc_manager_agent")


class StartupSafetyPolicy:
    """Classify startup observations and permit only a narrow ordinary-user subset."""

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
        """Return a non-overridable allow/block result for the exact current state."""
        path = _canonical(observation.executable_path) if observation.executable_path else None
        text = " ".join(
            value.casefold()
            for value in (
                observation.display_name,
                observation.publisher or "",
                str(path or ""),
            )
        )
        reasons: list[StartupErrorCode] = []
        safety_class = StartupSafetyClass.UNKNOWN

        if observation.scope != "CURRENT_USER":
            safety_class = StartupSafetyClass.ENTERPRISE_MANAGED
            reasons.append(StartupErrorCode.BLOCKED_ENTERPRISE_MANAGED)
        elif any(marker in text for marker in _AGENT_MARKERS) or (
            path is not None and _is_within(path, self._agent_root)
        ):
            safety_class = StartupSafetyClass.AGENT_COMPONENT
            reasons.append(StartupErrorCode.BLOCKED_AGENT_COMPONENT)
        elif path is None or not path.is_absolute() or not path.exists() or not path.is_file():
            reasons.append(StartupErrorCode.UNKNOWN_EXECUTABLE)
        elif _is_within(path, self._windows_directory):
            safety_class = StartupSafetyClass.SYSTEM_COMPONENT
            reasons.append(StartupErrorCode.BLOCKED_SYSTEM_COMPONENT)
        elif any(marker in text for marker in _SECURITY_MARKERS):
            safety_class = StartupSafetyClass.SECURITY_SOFTWARE
            reasons.append(StartupErrorCode.BLOCKED_SECURITY_SOFTWARE)
        elif any(marker in text for marker in _DRIVER_MARKERS):
            safety_class = StartupSafetyClass.DRIVER_RELATED
            reasons.append(StartupErrorCode.BLOCKED_DRIVER_RELATED)
        elif any(marker in text for marker in _ENTERPRISE_MARKERS):
            safety_class = StartupSafetyClass.ENTERPRISE_MANAGED
            reasons.append(StartupErrorCode.BLOCKED_ENTERPRISE_MANAGED)
        elif observation.publisher is None:
            reasons.append(StartupErrorCode.UNKNOWN_PUBLISHER)
        elif any(marker in observation.publisher.casefold() for marker in _MICROSOFT_MARKERS):
            safety_class = StartupSafetyClass.USER_MICROSOFT
            reasons.append(StartupErrorCode.BLOCKED_SYSTEM_COMPONENT)
        else:
            safety_class = StartupSafetyClass.USER_THIRD_PARTY

        expected_mode = (
            StartupManagementMode.DISABLE_SUPPORTED
            if action is StartupActionType.DISABLE
            else StartupManagementMode.RESTORE_SUPPORTED
        )
        if observation.management_mode is not expected_mode:
            reasons.append(
                StartupErrorCode.ALREADY_DISABLED
                if action is StartupActionType.DISABLE
                else StartupErrorCode.ALREADY_ENABLED
            )
        supported_source = observation.identity.source in {
            StartupSource.HKCU_RUN,
            StartupSource.USER_STARTUP_FOLDER,
        }
        if not supported_source:
            reasons.append(StartupErrorCode.UNSUPPORTED_SOURCE)

        decision = StartupSafetyDecision.BLOCK if reasons else StartupSafetyDecision.ALLOW
        return StartupSafetyAssessment(
            identity_digest=observation.identity.canonical_digest(),
            safety_class=safety_class,
            decision=decision,
            management_mode=observation.management_mode,
            reason_codes=tuple(dict.fromkeys(reasons)),
            explanation=(
                "Current-user ordinary third-party entry is eligible for the exact backed-up action"
                if decision is StartupSafetyDecision.ALLOW
                else _explain(reasons[0])
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


def _explain(reason: StartupErrorCode) -> str:
    return {
        StartupErrorCode.BLOCKED_ENTERPRISE_MANAGED: (
            "Machine-wide or enterprise-managed startup configuration is read-only in Stage 4B"
        ),
        StartupErrorCode.BLOCKED_AGENT_COMPONENT: "The Agent cannot disable its own components",
        StartupErrorCode.UNKNOWN_EXECUTABLE: (
            "The executable target cannot be resolved and verified safely"
        ),
        StartupErrorCode.BLOCKED_SYSTEM_COMPONENT: (
            "Microsoft and Windows components are protected by default"
        ),
        StartupErrorCode.BLOCKED_SECURITY_SOFTWARE: (
            "Security and endpoint-protection startup entries are protected"
        ),
        StartupErrorCode.BLOCKED_DRIVER_RELATED: (
            "Driver and firmware support entries are protected"
        ),
        StartupErrorCode.UNKNOWN_PUBLISHER: (
            "The executable publisher is unavailable; the entry remains read-only"
        ),
        StartupErrorCode.ALREADY_DISABLED: (
            "The entry is not in an Agent-manageable enabled state"
        ),
        StartupErrorCode.ALREADY_ENABLED: (
            "Only an Agent-disabled record with a verified backup can be restored"
        ),
        StartupErrorCode.UNSUPPORTED_SOURCE: "This startup source is inventory-only in Stage 4B",
    }[reason]
