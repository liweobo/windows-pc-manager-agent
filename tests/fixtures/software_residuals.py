"""Synthetic Stage 4D3 graph with no destructive filesystem capability."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pc_manager_agent import __version__
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.software_residuals import SoftwareResidualAuditLogger
from pc_manager_agent.confirmation.state_machine import ConfirmationService
from pc_manager_agent.domain.software_residuals import (
    ContextPathEvidence,
    ResidualClassification,
    ResidualSource,
    UninstallContext,
    UninstallMechanism,
)
from pc_manager_agent.orchestration.residual_collectors import (
    InstallLocationResidualCollector,
    KnownAppDataResidualCollector,
    KnownConfigurationResidualCollector,
    KnownServiceArtifactCollector,
    MsixDataResidualCollector,
    ShortcutResidualCollector,
)
from pc_manager_agent.orchestration.software_residual_analysis import (
    ResidualAnalysisPlanCompiler,
    ResidualAnalysisService,
    ResidualAnalyzer,
    ResidualSafetyReviewer,
)
from pc_manager_agent.persistence.software_residuals import SoftwareResidualRepository
from pc_manager_agent.safety.residual_classification import ResidualClassifier
from pc_manager_agent.safety.residual_ownership import ResidualOwnershipEvaluator
from pc_manager_agent.safety.residual_scope_policy import ResidualScanScopePolicy
from pc_manager_agent.safety.user_data_protection import UserDataProtectionPolicy
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.software_residuals import (
    SoftwareResidualAnalyzeTool,
    SoftwareResidualInspectTool,
    SoftwareResidualReportTool,
)

_DIGEST = "a" * 64


def residual_context(
    root: Path,
    *,
    mechanism: UninstallMechanism = UninstallMechanism.MSI,
    verified_removed: bool = True,
    verification_state: str | None = None,
    source: ResidualSource = ResidualSource.INSTALL_LOCATION,
    classification: ResidualClassification = ResidualClassification.PROGRAM_RESIDUAL,
    max_depth: int = 6,
) -> UninstallContext:
    """Build one eligible exact-root context for tests."""
    return UninstallContext(
        transaction_id=uuid4(),
        mechanism=mechanism,
        software_identity_digest=_DIGEST,
        display_name="Synthetic App",
        display_version="1.0",
        publisher="Synthetic Publisher",
        scope="current_user",
        architecture="x64",
        original_install_location=root,
        msi_product_code="{00000000-0000-0000-0000-000000000000}"
        if mechanism is UninstallMechanism.MSI
        else None,
        package_id="Synthetic.App" if mechanism is UninstallMechanism.WINGET else None,
        package_source="winget" if mechanism is UninstallMechanism.WINGET else None,
        msix_family_name="Synthetic.App_test" if mechanism is UninstallMechanism.MSIX else None,
        msix_full_name="Synthetic.App_1.0_x64__test"
        if mechanism is UninstallMechanism.MSIX
        else None,
        known_paths=(
            ContextPathEvidence(
                path=root,
                source=source,
                evidence_code="synthetic-exact-path",
                expected_classification=classification,
                max_depth=max_depth,
            ),
        ),
        uninstall_started_at=datetime.now(UTC),
        uninstall_completed_at=datetime.now(UTC),
        verification_state=verification_state
        or ("verified_removed" if verified_removed else "completed_unverified"),
        verified_removed=verified_removed,
        context_complete=True,
    )


@dataclass(slots=True)
class SyntheticResidualEnvironment:
    """Owned repositories and service for one isolated Stage 4D3 test."""

    repository: SoftwareResidualRepository
    audit: AuditRepository
    registry: ToolRegistry
    service: ResidualAnalysisService

    def close(self) -> None:
        """Release both independent SQLite engines."""
        self.repository.close()
        self.audit.close()


def build_residual_environment(
    database_path: Path,
    *,
    max_objects: int = 25_000,
    timeout_seconds: float = 60.0,
) -> SyntheticResidualEnvironment:
    """Compose production Stage 4D3 components over a temporary database."""
    repository = SoftwareResidualRepository(database_path)
    repository.initialize()
    audit = AuditRepository(database_path)
    audit.initialize()
    scope = ResidualScanScopePolicy(max_roots=16)
    classifier = ResidualClassifier()
    ownership = ResidualOwnershipEvaluator()
    protection = UserDataProtectionPolicy(database_path.parent / "user-profile")
    dependencies = (scope, classifier, ownership, protection)
    collectors = (
        InstallLocationResidualCollector(*dependencies),
        KnownAppDataResidualCollector(*dependencies),
        ShortcutResidualCollector(*dependencies),
        MsixDataResidualCollector(*dependencies),
        KnownServiceArtifactCollector(*dependencies),
        KnownConfigurationResidualCollector(*dependencies),
    )
    registry = ToolRegistry()
    analyzer = ResidualAnalyzer(repository, scope, collectors)
    registry.register(SoftwareResidualAnalyzeTool(analyzer))
    registry.register(SoftwareResidualReportTool(repository))
    registry.register(SoftwareResidualInspectTool(repository))
    service = ResidualAnalysisService(
        repository,
        ResidualAnalysisPlanCompiler(scope, max_objects, timeout_seconds),
        ResidualSafetyReviewer(registry, scope),
        ConfirmationService(),
        registry,
        SoftwareResidualAuditLogger(audit, app_version=__version__, git_commit="test"),
    )
    return SyntheticResidualEnvironment(repository, audit, registry, service)
