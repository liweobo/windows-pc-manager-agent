"""Defense-in-depth allow-list policy for Windows service control."""

from __future__ import annotations

import os
from pathlib import Path

from pc_manager_agent.domain.service_actions import (
    ServiceActionType,
    ServiceErrorCode,
    ServiceObservation,
    ServiceSafetyAssessment,
    ServiceSafetyClass,
    ServiceSafetyDecision,
    ServiceStartupType,
    ServiceState,
    canonical_path,
)

_AGENT_MARKERS = ("windows-pc-manager-agent", "pc_manager_agent", "pc manager agent")
_SECURITY_MARKERS = (
    "antivirus",
    "antimalware",
    "defender",
    "endpoint",
    "firewall",
    "security",
    "sentinel",
    "edr",
)
_NETWORK_MARKERS = (
    "dhcp",
    "dns",
    "lanman",
    "nla",
    "network",
    "tcpip",
    "wlan",
    "winsock",
)
_LOGIN_MARKERS = ("credential", "cryptsvc", "logon", "lsass", "sam", "sso", "user manager")
_STORAGE_MARKERS = (
    "bitlocker",
    "disk",
    "filesystem",
    "mount",
    "nvme",
    "storage",
    "vss",
)
_UPDATE_MARKERS = ("installer", "trustedinstaller", "update", "usosvc", "wuauserv")
_ENTERPRISE_MARKERS = ("domain", "enterprise", "group policy", "intune", "management")
_MICROSOFT_MARKERS = ("microsoft", "windows")
_PROTECTED_NAMES = frozenset(
    name.casefold()
    for name in (
        "Appinfo",
        "BFE",
        "BrokerInfrastructure",
        "CoreMessagingRegistrar",
        "CryptSvc",
        "DcomLaunch",
        "Dhcp",
        "Dnscache",
        "EventLog",
        "EventSystem",
        "gpsvc",
        "LanmanServer",
        "LanmanWorkstation",
        "LSM",
        "mpssvc",
        "NlaSvc",
        "nsi",
        "PlugPlay",
        "Power",
        "ProfSvc",
        "RpcEptMapper",
        "RpcSs",
        "SamSs",
        "Schedule",
        "SecurityHealthService",
        "SENS",
        "SystemEventsBroker",
        "Themes",
        "TokenBroker",
        "UserManager",
        "WinDefend",
        "Winmgmt",
        "WpnService",
        "wscsvc",
        "WSearch",
        "wuauserv",
    )
)

# Win32 service type bits. Only an independent user-owned process is controllable.
_SERVICE_KERNEL_DRIVER = 0x1
_SERVICE_FILE_SYSTEM_DRIVER = 0x2
_SERVICE_WIN32_OWN_PROCESS = 0x10
_SERVICE_WIN32_SHARE_PROCESS = 0x20
_SERVICE_INTERACTIVE_PROCESS = 0x100


class ServiceSafetyPolicy:
    """Permit only clearly ordinary, user-account, noncritical third-party services."""

    def __init__(
        self,
        *,
        current_username: str,
        agent_root: Path,
        windows_directory: Path | None = None,
    ) -> None:
        if not current_username.strip():
            raise ValueError("Current username is required for service safety")
        self._current_username = _normalize_account(current_username)
        self._agent_root = canonical_path(agent_root)
        self._windows_directory = canonical_path(
            windows_directory or Path(os.environ.get("WINDIR", r"C:\Windows"))
        )

    def assess(
        self,
        observation: ServiceObservation,
        action: ServiceActionType,
    ) -> ServiceSafetyAssessment:
        """Classify and make a fail-closed non-overridable control decision."""
        identity = observation.identity
        path = canonical_path(observation.binary_path) if observation.binary_path else None
        text = " ".join(
            value.casefold()
            for value in (
                identity.service_name,
                observation.display_name,
                observation.description or "",
                observation.publisher or "",
                str(path or ""),
            )
        )
        reasons: list[ServiceErrorCode] = []
        safety_class = ServiceSafetyClass.UNKNOWN

        driver = bool(
            identity.service_type & (_SERVICE_KERNEL_DRIVER | _SERVICE_FILE_SYSTEM_DRIVER)
        )
        shared = bool(identity.service_type & _SERVICE_WIN32_SHARE_PROCESS)
        own = bool(identity.service_type & _SERVICE_WIN32_OWN_PROCESS)
        interactive = bool(identity.service_type & _SERVICE_INTERACTIVE_PROCESS)
        if driver:
            safety_class = ServiceSafetyClass.DRIVER_SERVICE
            reasons.append(ServiceErrorCode.BLOCKED_DRIVER_SERVICE)
        elif identity.service_name.casefold() in _PROTECTED_NAMES:
            safety_class = ServiceSafetyClass.WINDOWS_CORE_SERVICE
            reasons.append(ServiceErrorCode.BLOCKED_PROTECTED_SERVICE)
        elif any(marker in text for marker in _AGENT_MARKERS) or (
            path is not None and _is_within(path, self._agent_root)
        ):
            safety_class = ServiceSafetyClass.AGENT_SERVICE
            reasons.append(ServiceErrorCode.BLOCKED_AGENT_SERVICE)
        elif any(marker in text for marker in _SECURITY_MARKERS):
            safety_class = ServiceSafetyClass.SECURITY_SERVICE
            reasons.append(ServiceErrorCode.BLOCKED_PROTECTED_SERVICE)
        elif any(marker in text for marker in _NETWORK_MARKERS):
            safety_class = ServiceSafetyClass.NETWORK_CRITICAL_SERVICE
            reasons.append(ServiceErrorCode.BLOCKED_PROTECTED_SERVICE)
        elif any(marker in text for marker in _LOGIN_MARKERS):
            safety_class = ServiceSafetyClass.LOGIN_CRITICAL_SERVICE
            reasons.append(ServiceErrorCode.BLOCKED_PROTECTED_SERVICE)
        elif any(marker in text for marker in _STORAGE_MARKERS):
            safety_class = ServiceSafetyClass.STORAGE_CRITICAL_SERVICE
            reasons.append(ServiceErrorCode.BLOCKED_PROTECTED_SERVICE)
        elif any(marker in text for marker in _UPDATE_MARKERS):
            safety_class = ServiceSafetyClass.UPDATE_SERVICE
            reasons.append(ServiceErrorCode.BLOCKED_PROTECTED_SERVICE)
        elif any(marker in text for marker in _ENTERPRISE_MARKERS):
            safety_class = ServiceSafetyClass.ENTERPRISE_MANAGED
            reasons.append(ServiceErrorCode.BLOCKED_PROTECTED_SERVICE)
        elif path is None or not path.is_absolute() or not path.exists() or not path.is_file():
            reasons.append(ServiceErrorCode.BLOCKED_UNKNOWN_SERVICE)
        elif _is_within(path, self._windows_directory):
            safety_class = ServiceSafetyClass.WINDOWS_CORE_SERVICE
            reasons.append(ServiceErrorCode.BLOCKED_PROTECTED_SERVICE)
        elif shared or interactive or not own:
            safety_class = ServiceSafetyClass.THIRD_PARTY_SYSTEM_SERVICE
            reasons.append(ServiceErrorCode.ACTION_NOT_SUPPORTED)
        elif not _same_account(identity.service_account, self._current_username):
            safety_class = ServiceSafetyClass.THIRD_PARTY_SYSTEM_SERVICE
            reasons.append(ServiceErrorCode.PRIVILEGE_REQUIRED)
        elif observation.publisher is None or not observation.publisher_verified:
            reasons.append(ServiceErrorCode.BLOCKED_UNKNOWN_SERVICE)
        elif any(marker in observation.publisher.casefold() for marker in _MICROSOFT_MARKERS):
            safety_class = ServiceSafetyClass.WINDOWS_CORE_SERVICE
            reasons.append(ServiceErrorCode.BLOCKED_PROTECTED_SERVICE)
        else:
            safety_class = ServiceSafetyClass.USER_THIRD_PARTY_SERVICE

        if observation.state.is_pending:
            reasons.append(ServiceErrorCode.PENDING_STATE)
        if action is ServiceActionType.START and observation.state is ServiceState.PAUSED:
            reasons.append(ServiceErrorCode.ACTION_NOT_SUPPORTED)
        if action is ServiceActionType.RESTART and observation.state is not ServiceState.RUNNING:
            reasons.append(ServiceErrorCode.ACTION_NOT_SUPPORTED)
        if (
            action in {ServiceActionType.STOP, ServiceActionType.RESTART}
            and observation.state is ServiceState.RUNNING
            and not observation.controls_accepted & 0x1
        ):
            reasons.append(ServiceErrorCode.ACTION_NOT_SUPPORTED)
        if (
            action in {ServiceActionType.START, ServiceActionType.RESTART}
            and observation.startup_configuration.startup_type is ServiceStartupType.DISABLED
        ):
            reasons.append(ServiceErrorCode.ACTION_NOT_SUPPORTED)
        decision = ServiceSafetyDecision.BLOCK if reasons else ServiceSafetyDecision.ALLOW
        return ServiceSafetyAssessment(
            identity_digest=identity.canonical_digest(),
            safety_class=safety_class,
            decision=decision,
            reason_codes=tuple(dict.fromkeys(reasons)),
            explanation=(
                "Ordinary current-user third-party service is eligible for exact state control"
                if decision is ServiceSafetyDecision.ALLOW
                else _explain(reasons[0])
            ),
        )


def _normalize_account(value: str) -> str:
    return value.strip().removeprefix(".\\").casefold()


def _same_account(candidate: str, current: str) -> bool:
    normalized = _normalize_account(candidate)
    if normalized == current:
        return True
    return "\\" not in normalized and normalized == current.rsplit("\\", 1)[-1]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _explain(reason: ServiceErrorCode) -> str:
    return {
        ServiceErrorCode.BLOCKED_DRIVER_SERVICE: "Driver services are always read-only",
        ServiceErrorCode.BLOCKED_PROTECTED_SERVICE: (
            "A protected or critical service cannot be controlled"
        ),
        ServiceErrorCode.BLOCKED_AGENT_SERVICE: "The Agent cannot control its own service",
        ServiceErrorCode.BLOCKED_UNKNOWN_SERVICE: (
            "The service identity cannot be classified reliably"
        ),
        ServiceErrorCode.ACTION_NOT_SUPPORTED: (
            "Shared, interactive, or unsupported service types are read-only"
        ),
        ServiceErrorCode.PRIVILEGE_REQUIRED: (
            "Only ordinary services running as the current user are eligible"
        ),
        ServiceErrorCode.PENDING_STATE: (
            "The service is in a transitional state; wait and create a new Preview"
        ),
    }[reason]
