"""Deterministic default-deny policy for Stage 4A process actions."""

from __future__ import annotations

import os
from pathlib import Path

from pc_manager_agent.domain.process_actions import (
    ProcessActionErrorCode,
    ProcessActionType,
    ProcessObservation,
    ProcessSafetyAssessment,
    ProcessSafetyClass,
    ProcessSafetyDecision,
)
from pc_manager_agent.platform_support.windows.process_management import protection_level_none

_CRITICAL_NAMES = frozenset(
    {
        "system",
        "registry",
        "memory compression",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "winlogon.exe",
        "services.exe",
        "lsass.exe",
        "svchost.exe",
        "fontdrvhost.exe",
        "dwm.exe",
    }
)
_SECURITY_NAMES = frozenset(
    {
        "msmpeng.exe",
        "nissrv.exe",
        "securityhealthservice.exe",
        "securityhealthsystray.exe",
        "sense.exe",
        "mssense.exe",
        "windefend.exe",
    }
)
_SECURITY_PATH_MARKERS = (
    "windows defender",
    "endpoint protection",
    "endpoint security",
    "antivirus",
    "anti-virus",
    "edr",
)
_SYSTEM_SIDS = frozenset({"S-1-5-18", "S-1-5-19", "S-1-5-20"})


class ProcessSafetyPolicy:
    """Classify current observations and deny every uncertain or protected target."""

    def __init__(
        self,
        *,
        current_owner_sid: str,
        current_session_id: int,
        agent_pids: frozenset[int] | None = None,
        windows_directory: Path | None = None,
    ) -> None:
        self._current_owner_sid = current_owner_sid
        self._current_session_id = current_session_id
        self._agent_pids = agent_pids or frozenset({os.getpid()})
        raw_windows = windows_directory or Path(os.environ.get("WINDIR", r"C:\Windows"))
        self._windows_directory = Path(os.path.normcase(str(raw_windows.resolve(strict=False))))

    def assess(
        self,
        observation: ProcessObservation,
        action: ProcessActionType,
        *,
        application_has_window: bool | None = None,
    ) -> ProcessSafetyAssessment:
        """Return the final non-overridable policy result for one live identity."""
        identity = observation.identity
        name = identity.process_name.casefold()
        executable = Path(os.path.normcase(str(identity.executable_path.resolve(strict=False))))
        reasons: list[ProcessActionErrorCode] = []
        classification = ProcessSafetyClass.UNKNOWN_SENSITIVE

        if identity.pid in self._agent_pids:
            classification = ProcessSafetyClass.AGENT_PROCESS
            reasons.append(ProcessActionErrorCode.BLOCKED_AGENT_PROCESS)
        elif identity.owner_sid in _SYSTEM_SIDS:
            classification = ProcessSafetyClass.SYSTEM_PROCESS
            reasons.append(ProcessActionErrorCode.BLOCKED_SYSTEM_PROCESS)
        elif identity.owner_sid != self._current_owner_sid:
            reasons.append(ProcessActionErrorCode.BLOCKED_DIFFERENT_USER)
        elif identity.session_id != self._current_session_id:
            reasons.append(ProcessActionErrorCode.BLOCKED_DIFFERENT_SESSION)
        elif observation.is_critical or name in _CRITICAL_NAMES:
            classification = ProcessSafetyClass.SYSTEM_PROCESS
            reasons.append(ProcessActionErrorCode.BLOCKED_SYSTEM_PROCESS)
        elif observation.protection_level != protection_level_none():
            classification = ProcessSafetyClass.UNKNOWN_SENSITIVE
            reasons.append(ProcessActionErrorCode.BLOCKED_UNKNOWN_SENSITIVE)
        elif name in _SECURITY_NAMES or any(
            marker in str(executable).casefold() for marker in _SECURITY_PATH_MARKERS
        ):
            classification = ProcessSafetyClass.SECURITY_PROCESS
            reasons.append(ProcessActionErrorCode.BLOCKED_SECURITY_PROCESS)
        elif observation.service_names:
            classification = ProcessSafetyClass.SERVICE_PROCESS
            reasons.append(ProcessActionErrorCode.BLOCKED_SERVICE_PROCESS)
        elif _is_within(executable, self._windows_directory):
            classification = ProcessSafetyClass.SYSTEM_PROCESS
            reasons.append(ProcessActionErrorCode.BLOCKED_SYSTEM_PROCESS)
        else:
            classification = (
                ProcessSafetyClass.USER_APPLICATION
                if observation.window_count > 0
                else ProcessSafetyClass.USER_BACKGROUND_PROCESS
            )

        if (
            not reasons
            and action is ProcessActionType.REQUEST_GRACEFUL_EXIT
            and not (
                observation.graceful_supported
                if application_has_window is None
                else application_has_window
            )
        ):
            reasons.append(ProcessActionErrorCode.UNSUPPORTED_GRACEFUL_EXIT)

        decision = ProcessSafetyDecision.BLOCK if reasons else ProcessSafetyDecision.ALLOW
        explanation = (
            "Deterministic policy allows this current-user ordinary process"
            if decision is ProcessSafetyDecision.ALLOW
            else _explain(reasons[0])
        )
        return ProcessSafetyAssessment(
            identity_digest=identity.canonical_digest(),
            safety_class=classification,
            decision=decision,
            reason_codes=tuple(reasons),
            explanation=explanation,
        )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _explain(reason: ProcessActionErrorCode) -> str:
    return {
        ProcessActionErrorCode.BLOCKED_AGENT_PROCESS: (
            "The Agent must use its controlled application-exit flow, not kill itself"
        ),
        ProcessActionErrorCode.BLOCKED_SYSTEM_PROCESS: (
            "Windows system and critical processes are never terminated in Stage 4A"
        ),
        ProcessActionErrorCode.BLOCKED_SECURITY_PROCESS: (
            "Security and endpoint-protection processes are protected"
        ),
        ProcessActionErrorCode.BLOCKED_SERVICE_PROCESS: (
            "This process is managed by Windows Service Control Manager"
        ),
        ProcessActionErrorCode.BLOCKED_DIFFERENT_USER: (
            "Stage 4A manages only ordinary processes owned by the current user"
        ),
        ProcessActionErrorCode.BLOCKED_DIFFERENT_SESSION: (
            "Stage 4A cannot control a process in another Windows session"
        ),
        ProcessActionErrorCode.BLOCKED_UNKNOWN_SENSITIVE: (
            "The process is protected or cannot be classified safely"
        ),
        ProcessActionErrorCode.UNSUPPORTED_GRACEFUL_EXIT: (
            "No target-owned top-level window can receive the supported graceful-exit request"
        ),
    }[reason]
