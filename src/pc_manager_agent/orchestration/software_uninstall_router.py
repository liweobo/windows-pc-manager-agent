"""Read-only deterministic routing among MSI, Vendor, winget, and MSIX mechanisms."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pc_manager_agent.domain.software_uninstall_analysis import (
    CapabilitySupport,
    ResolvedSoftwareTarget,
    SoftwareTargetQuery,
    UninstallCapabilityType,
)
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.tools.manifest import CancellationToken


class SoftwareUninstallMechanism(StrEnum):
    """Finite routing outcomes; unsupported never falls back to another runner."""

    MSI = "msi"
    VENDOR = "vendor"
    WINGET = "winget"
    MSIX = "msix"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class SoftwareUninstallRoute:
    """Read-only mechanism decision and exact target-resolution evidence."""

    mechanism: SoftwareUninstallMechanism
    resolution: ResolvedSoftwareTarget
    reason: str


class SoftwareUninstallRouter:
    """Select one mechanism only from fresh structured local metadata."""

    def __init__(
        self,
        resolver: SoftwareTargetResolver,
        capability: UninstallCapabilityResolver,
        max_items: int = 5_000,
    ) -> None:
        self._resolver = resolver
        self._capability = capability
        self._max_items = max_items

    def route(
        self,
        query: SoftwareTargetQuery,
        cancellation: CancellationToken | None = None,
    ) -> SoftwareUninstallRoute:
        """Refresh identity and return one mechanism without creating any authorization."""
        token = cancellation or CancellationToken()
        resolution, snapshot = self._resolver.resolve(query, self._max_items, token)
        target = resolution.selected
        if target is None:
            return SoftwareUninstallRoute(
                SoftwareUninstallMechanism.AMBIGUOUS,
                resolution,
                "目标不唯一。请先在软件表格中选择一个完整的名称、版本、发布者和范围。",
            )
        identity_digest = target.identity.canonical_digest()
        raw = snapshot.raw_by_identity.get(identity_digest)
        if raw is None:
            return SoftwareUninstallRoute(
                SoftwareUninstallMechanism.UNSUPPORTED,
                resolution,
                "本地来源证据不完整，不能选择卸载机制。",
            )
        if (
            target.scope.value == "current_user"
            and target.identity.source.value == "msix"
            and target.identity.package_family_name
            and target.identity.package_full_name
        ):
            return SoftwareUninstallRoute(
                SoftwareUninstallMechanism.MSIX,
                resolution,
                "已识别为当前用户 MSIX 身份；将重新通过 WinRT 清单解析精确 Package。",
            )
        if (
            target.scope.value == "current_user"
            and target.identity.package_manager_id is not None
            and target.identity.package_manager_id.casefold() in {"winget", "microsoft.winget"}
            and target.identity.package_id is not None
            and raw.package_id == target.identity.package_id
        ):
            capability = self._capability.resolve(target, raw)
            if (
                capability.capability_type is UninstallCapabilityType.PACKAGE_MANAGER
                and capability.support is CapabilitySupport.METADATA_SUPPORTED
            ):
                return SoftwareUninstallRoute(
                    SoftwareUninstallMechanism.WINGET,
                    resolution,
                    "已识别为当前用户的精确 winget Package，将进入官方源、映射和双确认流程。",
                )
        if target.windows_installer is True:
            capability = self._capability.resolve(target, raw)
            if (
                capability.capability_type is UninstallCapabilityType.MSI
                and capability.support is CapabilitySupport.METADATA_SUPPORTED
            ):
                return SoftwareUninstallRoute(
                    SoftwareUninstallMechanism.MSI,
                    resolution,
                    "已识别为精确 Windows Installer 产品，将进入 MSI 双确认流程。",
                )
        if raw.uninstall_string:
            interactive_only = raw.model_copy(update={"quiet_uninstall_string": None})
            capability = self._capability.resolve(target, interactive_only)
            if (
                capability.capability_type is UninstallCapabilityType.VENDOR_UNINSTALLER
                and capability.support is CapabilitySupport.METADATA_SUPPORTED
            ):
                return SoftwareUninstallRoute(
                    SoftwareUninstallMechanism.VENDOR,
                    resolution,
                    "已识别交互式厂商卸载器，将进入签名、参数和文件身份验证流程。",
                )
        return SoftwareUninstallRoute(
            SoftwareUninstallMechanism.UNSUPPORTED,
            resolution,
            "当前元数据不符合 MSI、Vendor、winget 或 MSIX 的狭窄安全边界。",
        )
