"""Unit coverage for Stage 4D2C2 identity, policy, confirmation, and verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.confirmation.msix_uninstall import (
    MsixConfirmationError,
    MsixConfirmationService,
    MsixConfirmationState,
    MsixUninstallConfirmation,
)
from pc_manager_agent.domain.msix_uninstall import (
    MsixDependencyReference,
    MsixDependencySnapshot,
    MsixDependencyState,
    MsixFamilyIdentity,
    MsixInstanceIdentity,
    MsixInventoryState,
    MsixPackageIdentity,
    MsixPackageInventory,
    MsixPackageType,
    MsixPreflight,
    MsixPreflightState,
    MsixRemovalAssessment,
    MsixRemovalDecision,
    MsixRemovalResultCategory,
    MsixScope,
    MsixTargetQuery,
    MsixUninstallPlan,
    MsixUninstallPreview,
    MsixVerificationState,
    NormalizedMsixPackage,
    ValidatedMsixRemovalAction,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareSafetyClass
from pc_manager_agent.orchestration.msix_uninstall import (
    MsixPackageTypeClassifier,
    MsixPreviewService,
    MsixRemovalPolicy,
    MsixResidualAnalyzer,
    MsixTargetResolver,
    MsixUninstallVerifier,
)


def package(
    *,
    full_name: str = "Example.App_1.0.0.0_x64__publisher",
    family_name: str = "Example.App_publisher",
    package_type: MsixPackageType = MsixPackageType.USER_MSIX_APP,
    signature_kind: str = "store",
    **flags: bool,
) -> NormalizedMsixPackage:
    """Build one synthetic package without using local inventory data."""
    identity = MsixPackageIdentity(
        family=MsixFamilyIdentity(
            family_name=family_name,
            name=family_name.split("_")[0],
            publisher_id="publisher",
        ),
        instance=MsixInstanceIdentity(
            full_name=full_name,
            version="1.0.0.0",
            architecture="x64",
        ),
        scope=MsixScope.CURRENT_USER,
        package_type=package_type,
        current_user_registered=True,
        is_framework=flags.get("is_framework", False),
        is_resource=flags.get("is_resource", False),
        is_bundle=flags.get("is_bundle", False),
        is_optional=flags.get("is_optional", False),
        is_development_mode=flags.get("is_development_mode", False),
        is_stub=flags.get("is_stub", False),
        signature_kind=signature_kind,
        status_ok=flags.get("status_ok", True),
    )
    return NormalizedMsixPackage(
        identity=identity,
        display_name="Example App",
        publisher_display_name="Example Publisher",
        installed_path="C:/Program Files/WindowsApps/Example.App_1.0.0.0_x64__publisher",
    )


def dependencies(target: NormalizedMsixPackage) -> MsixDependencySnapshot:
    """Build complete relationship evidence without collateral risk."""
    return MsixDependencySnapshot(
        state=MsixDependencyState.COMPLETE,
        target_identity_digest=target.identity.canonical_digest(),
        orphan_dependency_risk=False,
    )


def preflight() -> MsixPreflight:
    """Build a complete ready read-only preflight."""
    return MsixPreflight(
        state=MsixPreflightState.READY,
        process_probe_complete=True,
        service_probe_complete=True,
        related_process_count=0,
        related_service_count=0,
        another_uninstall_active=False,
    )


def assessment() -> MsixRemovalAssessment:
    """Build the only executable V1 safety result."""
    return MsixRemovalAssessment(
        decision=MsixRemovalDecision.ALLOW,
        safety_class=SoftwareSafetyClass.USER_APPLICATION,
        risk_level=RiskLevel.R2,
        reasons=("allowed",),
    )


def plan_preview() -> tuple[MsixUninstallPlan, MsixUninstallPreview]:
    """Build one exact plan and expiring Preview."""
    target = package()
    deps = dependencies(target)
    safe = assessment()
    ready = preflight()
    plan = MsixUninstallPlan(
        user_goal="remove example app",
        package_identity_digest=target.identity.canonical_digest(),
        dependency_digest=deps.canonical_digest(),
        assessment_digest=safe.canonical_digest(),
        preflight_digest=ready.canonical_digest(),
    )
    preview = MsixPreviewService().build(plan, target, deps, safe, ready)
    return plan, preview


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        ("is_framework", MsixPackageType.FRAMEWORK),
        ("is_resource", MsixPackageType.RESOURCE),
        ("is_bundle", MsixPackageType.BUNDLE),
        ("is_optional", MsixPackageType.OPTIONAL),
    ],
)
def test_classifier_protects_structural_package_types(flag: str, expected: MsixPackageType) -> None:
    """Framework, resource, bundle, and optional flags cannot be downgraded."""
    assert MsixPackageTypeClassifier().classify(package(**{flag: True})) is expected


def test_classifier_protects_system_and_security_packages() -> None:
    """System signatures and security names remain blocked classifications."""
    classifier = MsixPackageTypeClassifier()
    assert classifier.classify(package(signature_kind="system")) is MsixPackageType.SYSTEM
    assert (
        classifier.classify(package(family_name="Example.Security_publisher"))
        is MsixPackageType.SECURITY
    )


def test_display_query_never_directly_selects_package() -> None:
    """A display-name match returns candidates, never an executable identity."""
    target = package()
    result = MsixTargetResolver().resolve(
        MsixTargetQuery(display_query="Example"),
        MsixPackageInventory(state=MsixInventoryState.COMPLETE, packages=(target,)),
    )
    assert result.selected is None
    assert result.ambiguous is True
    assert result.candidates == (target,)


def test_exact_full_name_resolves_one_package() -> None:
    """An exact fresh Package Full Name may resolve one instance."""
    target = package()
    result = MsixTargetResolver().resolve(
        MsixTargetQuery(full_name=target.identity.instance.full_name),
        MsixPackageInventory(state=MsixInventoryState.COMPLETE, packages=(target,)),
    )
    assert result.selected == target


@pytest.mark.parametrize(
    "package_type",
    [
        MsixPackageType.FRAMEWORK,
        MsixPackageType.RESOURCE,
        MsixPackageType.BUNDLE,
        MsixPackageType.OPTIONAL,
        MsixPackageType.SYSTEM,
        MsixPackageType.PROVISIONED,
        MsixPackageType.DEPENDENCY,
        MsixPackageType.SECURITY,
        MsixPackageType.UNKNOWN,
    ],
)
def test_policy_blocks_every_non_user_app_type(package_type: MsixPackageType) -> None:
    """V1 has no override for any protected or uncertain package category."""
    target = package(package_type=package_type)
    result = MsixRemovalPolicy().assess(
        target,
        package_type,
        SoftwareSafetyClass.USER_APPLICATION,
        dependencies(target),
        preflight(),
        process_elevated=False,
    )
    assert result.decision is MsixRemovalDecision.BLOCK


def test_policy_blocks_reverse_and_orphan_dependency_risks() -> None:
    """Known dependent and possible Windows collateral removal both fail closed."""
    target = package()
    relation = MsixDependencyReference(
        family_name="Other.App_publisher",
        full_name="Other.App_1.0.0.0_x64__publisher",
        package_type=MsixPackageType.USER_MSIX_APP,
    )
    snapshot = MsixDependencySnapshot(
        state=MsixDependencyState.COMPLETE,
        target_identity_digest=target.identity.canonical_digest(),
        reverse_dependents=(relation,),
        orphan_dependency_risk=True,
    )
    result = MsixRemovalPolicy().assess(
        target,
        MsixPackageType.USER_MSIX_APP,
        SoftwareSafetyClass.USER_APPLICATION,
        snapshot,
        preflight(),
        process_elevated=False,
    )
    assert result.decision is MsixRemovalDecision.BLOCK
    assert len(result.reasons) == 2


def test_policy_blocks_elevated_agent_and_incomplete_preflight() -> None:
    """Administrator state is not authorization and incomplete evidence stays blocked."""
    target = package()
    unknown = preflight().model_copy(update={"state": MsixPreflightState.UNKNOWN})
    result = MsixRemovalPolicy().assess(
        target,
        MsixPackageType.USER_MSIX_APP,
        SoftwareSafetyClass.USER_APPLICATION,
        dependencies(target),
        unknown,
        process_elevated=True,
    )
    assert result.decision is MsixRemovalDecision.BLOCK


def test_preview_states_real_windows_data_impact() -> None:
    """Immediate UI facts preserve roaming data and disclose LocalState/collateral risk."""
    _, preview = plan_preview()
    assert preview.data_impact.roamable_data_preserved is True
    assert preview.data_impact.local_state_may_be_removed is True
    assert preview.data_impact.orphan_dependencies_may_be_removed_by_windows is True
    assert preview.data_impact.agent_extra_data_deletion is False


@pytest.mark.parametrize(
    ("fresh", "expected"),
    [
        (("original",), MsixVerificationState.PACKAGE_STILL_REGISTERED),
        (("replacement",), MsixVerificationState.PACKAGE_INSTANCE_REPLACED),
        ((), MsixVerificationState.VERIFIED_REMOVED),
    ],
)
def test_verifier_distinguishes_presence_replacement_and_removal(
    fresh: tuple[str, ...], expected: MsixVerificationState
) -> None:
    """A deployment result never replaces fresh family/instance checks."""
    original = package()
    packages: list[NormalizedMsixPackage] = []
    if "original" in fresh:
        packages.append(original)
    if "replacement" in fresh:
        packages.append(package(full_name="Example.App_2.0.0.0_x64__publisher"))
    result = MsixUninstallVerifier().verify(
        original,
        MsixPackageInventory(state=MsixInventoryState.COMPLETE, packages=tuple(packages)),
        MsixRemovalResultCategory.REMOVAL_COMPLETED,
    )
    assert result.state is expected


def test_verifier_reports_incomplete_interrupted_and_remaining_software() -> None:
    """Uncertain inventory, interruption, and mapped software never become success."""
    verifier = MsixUninstallVerifier()
    original = package()
    incomplete = verifier.verify(
        original,
        MsixPackageInventory(state=MsixInventoryState.FAILED, packages=()),
        MsixRemovalResultCategory.REMOVAL_COMPLETED,
    )
    assert incomplete.state is MsixVerificationState.COMPLETED_UNVERIFIED
    interrupted = verifier.verify(
        original,
        MsixPackageInventory(state=MsixInventoryState.COMPLETE, packages=()),
        MsixRemovalResultCategory.INTERRUPTED,
    )
    assert interrupted.state is MsixVerificationState.INTERRUPTED
    software = verifier.verify(
        original,
        MsixPackageInventory(state=MsixInventoryState.COMPLETE, packages=()),
        MsixRemovalResultCategory.REMOVAL_COMPLETED,
        software_identity_present=True,
    )
    assert software.state is MsixVerificationState.SOFTWARE_PRESENT


def test_residual_analyzer_uses_exact_path_only(tmp_path: Path) -> None:
    """Known-path reporting returns presence without enumerating or deleting content."""
    analyzer = MsixResidualAnalyzer()
    present = tmp_path / "package"
    present.mkdir()
    assert analyzer.inspect(str(present)).known_install_path_present is True
    assert analyzer.inspect(str(tmp_path / "missing")).known_install_path_present is False
    assert analyzer.inspect(None).known_install_path_present is None


def test_resolver_rejects_incomplete_display_inventory_but_allows_exact_full_name() -> None:
    """Partial rows block discovery but need not block an exact PFN revalidation."""
    target = package()
    inventory = MsixPackageInventory(state=MsixInventoryState.TRUNCATED, packages=(target,))
    display = MsixTargetResolver().resolve(MsixTargetQuery(display_query="Example"), inventory)
    assert display.selected is None
    exact = MsixTargetResolver().resolve(
        MsixTargetQuery(full_name=target.identity.instance.full_name), inventory
    )
    assert exact.selected == target


class MemoryConfirmationStore:
    """Test store that applies the same single-use semantics without SQLite."""

    def __init__(self) -> None:
        self.values: dict[object, MsixUninstallConfirmation] = {}

    def save_confirmation(self, confirmation: MsixUninstallConfirmation) -> None:
        self.values[confirmation.confirmation_id] = confirmation

    def get_confirmation(self, confirmation_id: object) -> MsixUninstallConfirmation:
        return self.values[confirmation_id]

    def update_confirmation(self, confirmation: MsixUninstallConfirmation) -> None:
        self.values[confirmation.confirmation_id] = confirmation

    def consume_pair(
        self,
        plan_confirmation: MsixUninstallConfirmation,
        runtime_confirmation: MsixUninstallConfirmation,
    ) -> None:
        for item in (plan_confirmation, runtime_confirmation):
            self.values[item.confirmation_id] = item.model_copy(
                update={"state": MsixConfirmationState.CONSUMED}
            )


def test_confirmation_pair_is_bound_and_single_use() -> None:
    """Plan plus immediate approval can be consumed only once."""
    plan, preview = plan_preview()
    store = MemoryConfirmationStore()
    service = MsixConfirmationService(store)
    first = service.request_plan(plan, preview)
    service.approve(first.confirmation_id, True, plan, preview)
    runtime = service.request_runtime(first.confirmation_id, plan, preview)
    service.approve(runtime.confirmation_id, True, plan, preview)
    assert (
        service.consume_runtime(runtime.confirmation_id, plan, preview).state
        is MsixConfirmationState.CONSUMED
    )
    with pytest.raises(MsixConfirmationError, match="absent or used"):
        service.consume_runtime(runtime.confirmation_id, plan, preview)


def test_confirmation_rejects_changed_package_instance() -> None:
    """A Store version update invalidates all previous approvals."""
    plan, preview = plan_preview()
    store = MemoryConfirmationStore()
    service = MsixConfirmationService(store)
    confirmation = service.request_plan(plan, preview)
    changed_package = package(full_name="Example.App_2.0.0.0_x64__publisher")
    changed = preview.model_copy(update={"package": changed_package})
    with pytest.raises(MsixConfirmationError):
        service.approve(confirmation.confirmation_id, True, plan, changed)


def test_confirmation_expires() -> None:
    """Expired confirmation cannot be approved."""
    plan, preview = plan_preview()
    current = datetime.now(UTC)
    store = MemoryConfirmationStore()
    service = MsixConfirmationService(store, now=lambda: current, plan_ttl_seconds=1)
    confirmation = service.request_plan(plan, preview)
    service._now = lambda: current + timedelta(seconds=2)
    with pytest.raises(MsixConfirmationError, match="expired"):
        service.approve(confirmation.confirmation_id, True, plan, preview)


def test_validated_action_forbids_scope_type_and_option_expansion() -> None:
    """No all-users, framework, or caller-selected data option enters the adapter."""
    target = package()
    kwargs = {
        "transaction_id": uuid4(),
        "identity": target.identity,
        "dependency_digest": "a" * 64,
        "assessment_digest": "b" * 64,
        "preflight_digest": "c" * 64,
    }
    assert ValidatedMsixRemovalAction(**kwargs).preserve_roamable_application_data is True
    with pytest.raises(ValueError, match="mandatory"):
        ValidatedMsixRemovalAction(**kwargs, preserve_roamable_application_data=False)
    framework = target.identity.model_copy(update={"package_type": MsixPackageType.FRAMEWORK})
    with pytest.raises(ValueError, match="ordinary"):
        ValidatedMsixRemovalAction(**{**kwargs, "identity": framework})
