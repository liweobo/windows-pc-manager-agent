"""Deterministic Stage 4D2C2 target, policy, Preview, and verification services."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pc_manager_agent.domain.msix_uninstall import (
    MsixDataImpact,
    MsixDependencySnapshot,
    MsixDependencyState,
    MsixInventoryState,
    MsixPackageInventory,
    MsixPackageType,
    MsixPreflight,
    MsixPreflightState,
    MsixRemovalAssessment,
    MsixRemovalDecision,
    MsixRemovalResultCategory,
    MsixResidualReport,
    MsixScope,
    MsixTargetQuery,
    MsixUninstallPlan,
    MsixUninstallPreview,
    MsixVerification,
    MsixVerificationState,
    NormalizedMsixPackage,
    ResolvedMsixPackage,
    ValidatedMsixRemovalAction,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareSafetyClass
from pc_manager_agent.platform_support.msix_packages import MsixPackagePlatform
from pc_manager_agent.tools.manifest import CancellationToken

_PROTECTED_FAMILY_PREFIXES = (
    "microsoft.windows.",
    "microsoft.windowsstore_",
    "microsoft.sechealthui_",
    "microsoft.desktopappinstaller_",
    "windows.immersivecontrolpanel_",
)
_PROTECTED_TOKENS = ("security", "defender", "firewall", "credential", "signin")


class MsixInventoryService:
    """Collect a fresh bounded current-user inventory through one platform adapter."""

    def __init__(self, platform: MsixPackagePlatform, max_items: int = 5_000) -> None:
        if max_items <= 0:
            raise ValueError("MSIX inventory limit must be positive")
        self._platform = platform
        self._max_items = max_items

    def collect(self, cancellation: CancellationToken | None = None) -> MsixPackageInventory:
        """Return current-user package evidence without querying other scopes."""
        return self._platform.inventory_current_user(
            self._max_items, cancellation or CancellationToken()
        )


class MsixTargetResolver:
    """Resolve exact package identities and keep display-name matches non-executable."""

    def resolve(
        self, query: MsixTargetQuery, inventory: MsixPackageInventory
    ) -> ResolvedMsixPackage:
        """Select one exact identity or return explicit ambiguity/absence."""
        exact: list[NormalizedMsixPackage] = []
        display: list[NormalizedMsixPackage] = []
        for package in inventory.packages:
            identity = package.identity
            if (
                query.identity_digest == identity.canonical_digest()
                or (query.full_name and query.full_name == identity.instance.full_name)
                or (query.family_name and query.family_name == identity.family.family_name)
            ):
                exact.append(package)
            elif (
                query.display_query
                and query.display_query.casefold() in package.display_name.casefold()
            ):
                display.append(package)
        exact_selector = query.identity_digest is not None or query.full_name is not None
        if len(exact) == 1 and (inventory.state is MsixInventoryState.COMPLETE or exact_selector):
            return ResolvedMsixPackage(
                query=query,
                selected=exact[0],
                ambiguous=False,
                reason=(
                    "Exact package identity matched; unrelated package metadata was incomplete."
                    if inventory.state is not MsixInventoryState.COMPLETE
                    else "Exact package identity matched."
                ),
            )
        candidates = exact if exact else display
        return ResolvedMsixPackage(
            query=query,
            candidates=tuple(candidates[:100]),
            ambiguous=True,
            reason=(
                "Current-user package inventory is incomplete for this selector."
                if inventory.state is not MsixInventoryState.COMPLETE
                else "Display text is discovery-only; choose one exact package instance."
                if candidates
                else "No current-user package matched the query."
            ),
        )


class MsixPackageTypeClassifier:
    """Apply protected system/security classification after structural WinRT flags."""

    def classify(self, package: NormalizedMsixPackage) -> MsixPackageType:
        """Return a protected type without allowing names to downgrade strong flags."""
        identity = package.identity
        if identity.is_framework:
            return MsixPackageType.FRAMEWORK
        if identity.is_resource:
            return MsixPackageType.RESOURCE
        if identity.is_bundle:
            return MsixPackageType.BUNDLE
        if identity.is_optional:
            return MsixPackageType.OPTIONAL
        if identity.signature_kind == "system":
            return MsixPackageType.SYSTEM
        text = f"{identity.family.family_name} {identity.family.name}".casefold()
        if text.startswith(_PROTECTED_FAMILY_PREFIXES):
            return MsixPackageType.SYSTEM
        if any(token in text for token in _PROTECTED_TOKENS):
            return MsixPackageType.SECURITY
        return identity.package_type


class MsixRemovalPolicy:
    """Fail closed over type, scope, safety class, dependencies, and preflight."""

    def assess(
        self,
        package: NormalizedMsixPackage,
        package_type: MsixPackageType,
        safety_class: SoftwareSafetyClass,
        dependencies: MsixDependencySnapshot,
        preflight: MsixPreflight,
        *,
        process_elevated: bool,
    ) -> MsixRemovalAssessment:
        """Allow only a healthy ordinary current-user app with complete safe evidence."""
        reasons: list[str] = []
        identity = package.identity
        if identity.scope is not MsixScope.CURRENT_USER or not identity.current_user_registered:
            reasons.append("Only a current-user registration is supported.")
        if package_type is not MsixPackageType.USER_MSIX_APP:
            reasons.append(f"Protected or unsupported package type: {package_type.value}.")
        if safety_class is not SoftwareSafetyClass.USER_APPLICATION:
            reasons.append(f"Software safety class is blocked: {safety_class.value}.")
        if not identity.status_ok or identity.is_development_mode or identity.is_stub:
            reasons.append("Package health, development-mode, or stub evidence is unsafe.")
        if dependencies.state is not MsixDependencyState.COMPLETE:
            reasons.append("Package dependency evidence is incomplete.")
        if dependencies.reverse_dependents:
            reasons.append("Other packages depend on this package.")
        if dependencies.orphan_dependency_risk:
            reasons.append("Windows could remove an otherwise orphaned dependency.")
        if preflight.state is not MsixPreflightState.READY:
            reasons.append("Process/service/concurrency preflight is not ready.")
        if process_elevated:
            reasons.append("MSIX removal is disabled while the Agent is elevated.")
        return MsixRemovalAssessment(
            decision=MsixRemovalDecision.BLOCK if reasons else MsixRemovalDecision.ALLOW,
            safety_class=safety_class,
            risk_level=RiskLevel.R2,
            reasons=tuple(reasons or ("All Stage 4D2C2 safety gates passed.",)),
        )


class MsixPreviewService:
    """Build an expiring Preview that states the approved Windows data semantics."""

    def __init__(self, ttl_seconds: int = 300, now: Callable[[], datetime] | None = None) -> None:
        if ttl_seconds <= 0:
            raise ValueError("MSIX Preview TTL must be positive")
        self._ttl = ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def build(
        self,
        plan: MsixUninstallPlan,
        package: NormalizedMsixPackage,
        dependencies: MsixDependencySnapshot,
        assessment: MsixRemovalAssessment,
        preflight: MsixPreflight,
    ) -> MsixUninstallPreview:
        """Bind one plan to exact package, relationship, policy, and data-impact facts."""
        current = self._now()
        executable = (
            assessment.decision is MsixRemovalDecision.ALLOW
            and dependencies.state is MsixDependencyState.COMPLETE
            and not dependencies.reverse_dependents
            and not dependencies.orphan_dependency_risk
            and preflight.state is MsixPreflightState.READY
        )
        return MsixUninstallPreview(
            plan_id=plan.plan_id,
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_digest=plan.canonical_digest(),
            generated_at=current,
            expires_at=current + timedelta(seconds=self._ttl),
            package=package,
            dependencies=dependencies,
            assessment=assessment,
            preflight=preflight,
            data_impact=MsixDataImpact(),
            executable=executable,
        )


class MsixExecutionValidator:
    """Revalidate exact package and dependency evidence immediately before dispatch."""

    def __init__(self, platform: MsixPackagePlatform, max_items: int = 5_000) -> None:
        self._platform = platform
        self._inventory = MsixInventoryService(platform, max_items)

    def validate(
        self,
        preview: MsixUninstallPreview,
        fresh_preflight: MsixPreflight,
        cancellation: CancellationToken,
    ) -> ValidatedMsixRemovalAction:
        """Abort when Store update, scope, type, relationship, or preflight evidence changed."""
        fresh = self._inventory.collect(cancellation)
        matches = [
            item
            for item in fresh.packages
            if item.identity.instance.full_name == preview.package.identity.instance.full_name
        ]
        if fresh.state is not MsixInventoryState.COMPLETE or len(matches) != 1:
            raise RuntimeError("MSIX package disappeared or inventory became incomplete")
        identity = matches[0].identity
        if identity.canonical_digest() != preview.package.identity.canonical_digest():
            raise RuntimeError("MSIX package identity changed; old confirmation is invalid")
        dependencies = self._platform.dependency_snapshot(identity)
        if dependencies.canonical_digest() != preview.dependencies.canonical_digest():
            raise RuntimeError("MSIX dependency state changed; old confirmation is invalid")
        if fresh_preflight.canonical_digest() != preview.preflight.canonical_digest():
            raise RuntimeError("MSIX execution preflight changed; old confirmation is invalid")
        return ValidatedMsixRemovalAction(
            transaction_id=preview.transaction_id,
            identity=identity,
            dependency_digest=dependencies.canonical_digest(),
            assessment_digest=preview.assessment.canonical_digest(),
            preflight_digest=fresh_preflight.canonical_digest(),
        )


class MsixUninstallVerifier:
    """Determine final status only from fresh current-user package evidence."""

    def verify(
        self,
        original: NormalizedMsixPackage,
        inventory: MsixPackageInventory,
        result_category: MsixRemovalResultCategory,
        *,
        software_identity_present: bool | None = None,
    ) -> MsixVerification:
        """Distinguish removal, persistence, replacement, and incomplete evidence."""
        if result_category is MsixRemovalResultCategory.INTERRUPTED:
            return MsixVerification(
                state=MsixVerificationState.INTERRUPTED,
                original_full_name_present=False,
                reason="Removal monitoring was interrupted; fresh verification is required.",
            )
        if inventory.state is not MsixInventoryState.COMPLETE:
            return MsixVerification(
                state=MsixVerificationState.COMPLETED_UNVERIFIED,
                original_full_name_present=False,
                software_identity_present=software_identity_present,
                reason="Fresh current-user package inventory is incomplete.",
            )
        family = original.identity.family.family_name
        original_full = original.identity.instance.full_name
        same_family = tuple(
            item.identity.instance.full_name
            for item in inventory.packages
            if item.identity.family.family_name == family
        )
        if original_full in same_family:
            return MsixVerification(
                state=MsixVerificationState.PACKAGE_STILL_REGISTERED,
                original_full_name_present=True,
                same_family_instances=same_family,
                software_identity_present=software_identity_present,
                reason="The exact package instance remains registered.",
            )
        if same_family:
            return MsixVerification(
                state=MsixVerificationState.PACKAGE_INSTANCE_REPLACED,
                original_full_name_present=False,
                same_family_instances=same_family,
                software_identity_present=software_identity_present,
                reason="The original instance disappeared but the same family has another version.",
            )
        if software_identity_present is True:
            return MsixVerification(
                state=MsixVerificationState.SOFTWARE_PRESENT,
                original_full_name_present=False,
                software_identity_present=True,
                reason="Package registration disappeared but mapped software evidence remains.",
            )
        state = (
            MsixVerificationState.ALREADY_REMOVED
            if result_category is MsixRemovalResultCategory.PACKAGE_NOT_FOUND
            else MsixVerificationState.VERIFIED_REMOVED
        )
        return MsixVerification(
            state=state,
            original_full_name_present=False,
            software_identity_present=software_identity_present,
            reason="Fresh inventory no longer contains the package family.",
        )


class MsixResidualAnalyzer:
    """Inspect only one exact known install path and never enumerate/delete user data."""

    def inspect(self, installed_path: str | None) -> MsixResidualReport:
        """Return an exact-path presence flag using lstat only."""
        present: bool | None = None
        if installed_path:
            try:
                Path(installed_path).lstat()
                present = True
            except FileNotFoundError:
                present = False
            except OSError:
                present = None
        return MsixResidualReport(known_install_path_present=present)


def ready_msix_preflight(*, another_uninstall_active: bool = False) -> MsixPreflight:
    """Build a complete baseline preflight for adapters that supply zero related objects."""
    return MsixPreflight(
        state=(
            MsixPreflightState.BLOCKED if another_uninstall_active else MsixPreflightState.READY
        ),
        process_probe_complete=True,
        service_probe_complete=True,
        related_process_count=0,
        related_service_count=0,
        another_uninstall_active=another_uninstall_active,
        blockers=(
            ("Another uninstall transaction is active.",) if another_uninstall_active else ()
        ),
    )
