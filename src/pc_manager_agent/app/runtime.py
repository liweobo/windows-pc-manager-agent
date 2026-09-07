"""Dependency composition for GUI and headless tests."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from pc_manager_agent import __version__
from pc_manager_agent.app.agents import AgentServices, build_agent_services
from pc_manager_agent.app.browser import BrowserServices, build_browser_services
from pc_manager_agent.app.office import OfficeServices, build_office_services
from pc_manager_agent.app.optimization_reviews import (
    OptimizationReviewServices,
    build_optimization_review_services,
)
from pc_manager_agent.app.tasks import FinalTaskServices, build_final_task_services
from pc_manager_agent.audit.elevated_broker import ElevatedBrokerAuditLogger
from pc_manager_agent.audit.file_operations import OperationAuditLogger
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.msix_uninstall import MsixUninstallAuditLogger
from pc_manager_agent.audit.privileged_actions import PrivilegedActionAuditLogger
from pc_manager_agent.audit.process_actions import ProcessActionAuditLogger
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.residual_cleanup import ResidualCleanupAuditLogger
from pc_manager_agent.audit.service_actions import ServiceActionAuditLogger
from pc_manager_agent.audit.service_startup_actions import ServiceStartupActionAuditLogger
from pc_manager_agent.audit.software_residuals import SoftwareResidualAuditLogger
from pc_manager_agent.audit.software_uninstall_analysis import (
    SoftwareUninstallAnalysisAuditLogger,
)
from pc_manager_agent.audit.software_uninstall_execution import MsiUninstallAuditLogger
from pc_manager_agent.audit.startup_actions import StartupActionAuditLogger
from pc_manager_agent.audit.system_cleanup import SystemCleanupAuditLogger
from pc_manager_agent.audit.system_diagnostics import DiagnosticAuditLogger
from pc_manager_agent.audit.system_optimization import SystemOptimizationAuditLogger
from pc_manager_agent.audit.trash import TrashAuditLogger
from pc_manager_agent.audit.vendor_uninstall import VendorUninstallAuditLogger
from pc_manager_agent.audit.winget_uninstall import WingetUninstallAuditLogger
from pc_manager_agent.authorization.models import AuthorizedPath
from pc_manager_agent.authorization.service import AuthorizedPathService
from pc_manager_agent.config.production import ReleaseFeature
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.confirmation.external_data import (
    ExternalDataConsentRequest,
    ExternalDataConsentService,
)
from pc_manager_agent.confirmation.file_operations import (
    OperationConfirmationService,
    RollbackConfirmationService,
)
from pc_manager_agent.confirmation.msix_uninstall import MsixConfirmationService
from pc_manager_agent.confirmation.privileged_actions import (
    PrivilegedActionConfirmationService,
)
from pc_manager_agent.confirmation.process_actions import ProcessActionConfirmationService
from pc_manager_agent.confirmation.residual_cleanup import (
    ResidualCleanupConfirmationService,
)
from pc_manager_agent.confirmation.service_actions import ServiceActionConfirmationService
from pc_manager_agent.confirmation.service_startup_actions import (
    ServiceStartupActionConfirmationService,
)
from pc_manager_agent.confirmation.software_uninstall_analysis import (
    SoftwareAnalysisConfirmationService,
)
from pc_manager_agent.confirmation.software_uninstall_execution import (
    MsiUninstallConfirmationService,
)
from pc_manager_agent.confirmation.startup_actions import StartupActionConfirmationService
from pc_manager_agent.confirmation.state_machine import ConfirmationService
from pc_manager_agent.confirmation.system_cleanup import SystemCleanupConfirmationService
from pc_manager_agent.confirmation.system_diagnostics import DiagnosticConfirmationService
from pc_manager_agent.confirmation.system_optimization import OptimizationConfirmationService
from pc_manager_agent.confirmation.trash import TrashConfirmationService
from pc_manager_agent.confirmation.vendor_uninstall import VendorUninstallConfirmationService
from pc_manager_agent.confirmation.winget_uninstall import WingetUninstallConfirmationService
from pc_manager_agent.domain.elevated_broker import BrokerTrustMode
from pc_manager_agent.domain.file_analysis import FileAnalysisProgress
from pc_manager_agent.domain.privileged_actions import (
    PrivilegedCallerContext,
    PrivilegedExecutionMode,
)
from pc_manager_agent.domain.reports import ScanProgress
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.transactions import OperationProgress
from pc_manager_agent.domain.trash import TrashExecutionReport
from pc_manager_agent.orchestration.diagnostic_engine import DiagnosticEngine
from pc_manager_agent.orchestration.diagnostic_provider import (
    DiagnosticExplainer,
    DiagnosticProviderPlanner,
)
from pc_manager_agent.orchestration.elevated_service_actions import (
    ElevatedServiceActionCoordinator,
)
from pc_manager_agent.orchestration.elevated_service_preparation import (
    ElevatedServicePreparationService,
)
from pc_manager_agent.orchestration.elevated_stage4x3 import (
    ElevatedStage4X3PreparationService,
)
from pc_manager_agent.orchestration.explanation import FileAnalysisExplainer
from pc_manager_agent.orchestration.file_analysis import FileAnalysisOrchestrator
from pc_manager_agent.orchestration.file_analysis_planner import (
    FileAnalysisPlanCompiler,
    FileAnalysisPlanner,
)
from pc_manager_agent.orchestration.file_operation_planner import (
    FileOperationPlanCompiler,
    FileOperationPlanner,
    FileOperationSourceResolver,
)
from pc_manager_agent.orchestration.file_operation_service import FileOperationService
from pc_manager_agent.orchestration.msix_execution_preflight import MsixExecutionPreflightService
from pc_manager_agent.orchestration.msix_uninstall_execution import MsixUninstallService
from pc_manager_agent.orchestration.optimization_evidence import (
    RepositoryOptimizationEvidenceSource,
)
from pc_manager_agent.orchestration.optimization_recommendation_engine import (
    OptimizationRecommendationEngine,
)
from pc_manager_agent.orchestration.optimization_report_store import (
    OptimizationReportSessionStore,
)
from pc_manager_agent.orchestration.performance_diagnostic_engine import (
    PerformanceDiagnosticEngine,
)
from pc_manager_agent.orchestration.privileged_actions import PrivilegedActionService
from pc_manager_agent.orchestration.privileged_postconditions import (
    Stage4X3PostconditionVerifier,
)
from pc_manager_agent.orchestration.process_action_planner import ProcessActionPlanCompiler
from pc_manager_agent.orchestration.process_actions import ProcessActionService
from pc_manager_agent.orchestration.process_target_resolver import ProcessTargetResolver
from pc_manager_agent.orchestration.residual_cleanup import ResidualCleanupService
from pc_manager_agent.orchestration.residual_collectors import (
    InstallLocationResidualCollector,
    KnownAppDataResidualCollector,
    KnownConfigurationResidualCollector,
    KnownServiceArtifactCollector,
    MsixDataResidualCollector,
    ShortcutResidualCollector,
)
from pc_manager_agent.orchestration.service import ScanOrchestrator
from pc_manager_agent.orchestration.service_action_planner import ServiceActionPlanCompiler
from pc_manager_agent.orchestration.service_actions import ServiceActionService
from pc_manager_agent.orchestration.service_dependency_analyzer import ServiceDependencyAnalyzer
from pc_manager_agent.orchestration.service_startup_actions import ServiceStartupActionService
from pc_manager_agent.orchestration.service_target_resolver import ServiceTargetResolver
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_execution_preflight import (
    SoftwareExecutionPreflight,
)
from pc_manager_agent.orchestration.software_impact_analyzer import SoftwareImpactAnalyzer
from pc_manager_agent.orchestration.software_inventory import SoftwareInventoryService
from pc_manager_agent.orchestration.software_msi_validation import MsiProductValidator
from pc_manager_agent.orchestration.software_residual_analysis import (
    ResidualAnalysisPlanCompiler,
    ResidualAnalysisService,
    ResidualAnalyzer,
    ResidualSafetyReviewer,
)
from pc_manager_agent.orchestration.software_residual_analyzer import SoftwareResidualAnalyzer
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.orchestration.software_uninstall_analysis import (
    SoftwareUninstallAnalysisPlanCompiler,
    SoftwareUninstallAnalysisService,
)
from pc_manager_agent.orchestration.software_uninstall_execution import MsiUninstallService
from pc_manager_agent.orchestration.software_uninstall_router import SoftwareUninstallRouter
from pc_manager_agent.orchestration.software_uninstall_verifier import MsiUninstallVerifier
from pc_manager_agent.orchestration.startup_actions import StartupActionService
from pc_manager_agent.orchestration.startup_target_resolver import StartupTargetResolver
from pc_manager_agent.orchestration.system_cleanup import SystemCleanupService
from pc_manager_agent.orchestration.system_diagnostic_planner import DiagnosticPlanCompiler
from pc_manager_agent.orchestration.system_diagnostics import (
    DiagnosticOrchestrator,
    SystemSnapshotService,
)
from pc_manager_agent.orchestration.system_optimization import SystemOptimizationOrchestrator
from pc_manager_agent.orchestration.system_optimization_planner import (
    SystemOptimizationPlanCompiler,
)
from pc_manager_agent.orchestration.transaction_executor import TransactionExecutor
from pc_manager_agent.orchestration.trash_planner import TrashPlanCompiler
from pc_manager_agent.orchestration.trash_service import TrashService
from pc_manager_agent.orchestration.uninstall_context import UninstallContextRecorder
from pc_manager_agent.orchestration.vendor_execution_preflight import VendorExecutionPreflight
from pc_manager_agent.orchestration.vendor_residual_analyzer import VendorResidualAnalyzer
from pc_manager_agent.orchestration.vendor_uninstall_execution import VendorUninstallService
from pc_manager_agent.orchestration.vendor_uninstall_metadata import VendorUninstallMetadataParser
from pc_manager_agent.orchestration.vendor_uninstall_verifier import VendorUninstallVerifier
from pc_manager_agent.orchestration.winget_execution_preflight import (
    WingetExecutionPreflightService,
)
from pc_manager_agent.orchestration.winget_inventory import (
    PackageInventoryService,
    WingetAvailabilityService,
)
from pc_manager_agent.orchestration.winget_residual_analyzer import WingetResidualAnalyzer
from pc_manager_agent.orchestration.winget_software_mapping import WingetSoftwareMapper
from pc_manager_agent.orchestration.winget_target_resolver import PackageTargetResolver
from pc_manager_agent.orchestration.winget_uninstall_execution import WingetUninstallService
from pc_manager_agent.orchestration.winget_uninstall_verifier import WingetUninstallVerifier
from pc_manager_agent.persistence.analysis_results import AnalysisResultRepository
from pc_manager_agent.persistence.authorized_paths import AuthorizedPathRepository
from pc_manager_agent.persistence.file_operations import (
    OperationRepository,
    TransactionExecutionGuard,
)
from pc_manager_agent.persistence.msix_uninstall import (
    MsixUninstallExecutionGuard,
    MsixUninstallRepository,
)
from pc_manager_agent.persistence.optimization_receipts import OptimizationDomainResultReader
from pc_manager_agent.persistence.optimization_sessions import OptimizationSessionRepository
from pc_manager_agent.persistence.privileged_actions import (
    PrivilegedActionRepository,
    PrivilegedRequestReplayStore,
)
from pc_manager_agent.persistence.process_actions import (
    ProcessActionRepository,
    ProcessExecutionGuard,
)
from pc_manager_agent.persistence.residual_cleanup import (
    ResidualCleanupExecutionGuard,
    ResidualCleanupRepository,
)
from pc_manager_agent.persistence.service_actions import (
    ServiceActionRepository,
    ServiceExecutionGuard,
)
from pc_manager_agent.persistence.service_startup_actions import (
    ServiceStartupActionRepository,
    ServiceStartupBackupVault,
    ServiceStartupExecutionGuard,
)
from pc_manager_agent.persistence.software_residuals import SoftwareResidualRepository
from pc_manager_agent.persistence.software_uninstall_execution import (
    MsiUninstallExecutionGuard,
    MsiUninstallRepository,
)
from pc_manager_agent.persistence.startup_actions import (
    StartupActionRepository,
    StartupBackupVault,
    StartupExecutionGuard,
)
from pc_manager_agent.persistence.system_cleanup import (
    SystemCleanupExecutionGuard,
    SystemCleanupRepository,
)
from pc_manager_agent.persistence.vendor_uninstall import (
    VendorUninstallExecutionGuard,
    VendorUninstallRepository,
)
from pc_manager_agent.persistence.winget_uninstall import (
    WingetUninstallExecutionGuard,
    WingetUninstallRepository,
)
from pc_manager_agent.platform_support.processes import ProcessManagementPlatform
from pc_manager_agent.platform_support.service_control import ServiceControlPlatform
from pc_manager_agent.platform_support.service_startup import ServiceStartupPlatform
from pc_manager_agent.platform_support.startup import StartupManagementPlatform
from pc_manager_agent.platform_support.windows.data_protection import (
    WindowsCurrentUserDataProtector,
)
from pc_manager_agent.platform_support.windows.elevation import WindowsUacBrokerLauncher
from pc_manager_agent.platform_support.windows.explorer import WindowsExplorerService
from pc_manager_agent.platform_support.windows.file_operations import (
    WindowsFileOperationPlatform,
)
from pc_manager_agent.platform_support.windows.msi_uninstall import (
    WindowsMsiProductInventory,
    WindowsMsiUninstallPlatform,
    current_process_is_elevated,
)
from pc_manager_agent.platform_support.windows.msix_packages import WindowsMsixPackagePlatform
from pc_manager_agent.platform_support.windows.named_pipe import WindowsBrokerPipeClient
from pc_manager_agent.platform_support.windows.path_info import (
    is_network_path,
    last_access_time_reliable,
)
from pc_manager_agent.platform_support.windows.process_identity import (
    WindowsBrokerBinaryInspector,
    capture_current_process_identity,
)
from pc_manager_agent.platform_support.windows.process_management import (
    WindowsProcessManagementPlatform,
)
from pc_manager_agent.platform_support.windows.recycle_bin import WindowsRecycleBinPlatform
from pc_manager_agent.platform_support.windows.recycle_bin_empty import (
    WindowsRecycleBinEmptyPlatform,
)
from pc_manager_agent.platform_support.windows.residual_explorer import (
    WindowsResidualExplorerService,
)
from pc_manager_agent.platform_support.windows.service_control import (
    WindowsServiceControlPlatform,
    current_windows_username,
)
from pc_manager_agent.platform_support.windows.service_startup import (
    WindowsServiceStartupPlatform,
)
from pc_manager_agent.platform_support.windows.software_inventory import (
    WindowsSoftwareInventoryPlatform,
)
from pc_manager_agent.platform_support.windows.startup_management import (
    WindowsStartupManagementPlatform,
)
from pc_manager_agent.platform_support.windows.system_cleanup import (
    WindowsCleanupActivityProbe,
)
from pc_manager_agent.platform_support.windows.system_diagnostics import (
    WindowsSystemDiagnosticsPlatform,
)
from pc_manager_agent.platform_support.windows.system_optimization import (
    WindowsSystemOptimizationPlatform,
)
from pc_manager_agent.platform_support.windows.vendor_uninstall import (
    WindowsVendorExecutablePlatform,
    WindowsVendorUninstallPlatform,
)
from pc_manager_agent.platform_support.windows.winget_uninstall import (
    IndependentWingetSoftwarePackageProvider,
    WindowsWingetAvailabilityPlatform,
    WindowsWingetPackageInventoryPlatform,
    WindowsWingetUninstallPlatform,
)
from pc_manager_agent.privileged.authentication import EphemeralHmacAuthenticator
from pc_manager_agent.privileged.availability import PrivilegedBrokerAvailabilityService
from pc_manager_agent.privileged.builder import PrivilegedActionBuilder
from pc_manager_agent.privileged.mock_broker import MockPrivilegedBroker
from pc_manager_agent.privileged.registry import build_stage4x1_registry
from pc_manager_agent.privileged.revalidation import (
    FakePrivilegedSystemState,
    ServicePrivilegedRevalidator,
)
from pc_manager_agent.privileged.serialization import PrivilegedRequestSerializer
from pc_manager_agent.providers.llm.base import LLMProvider
from pc_manager_agent.providers.llm.openai_provider import OpenAILLMProvider
from pc_manager_agent.reporting.exporter import ReportExporter, ReportExportResult
from pc_manager_agent.reporting.residual_exporter import ResidualReportExporter
from pc_manager_agent.reporting.system_optimization_exporter import (
    SystemOptimizationReportExporter,
)
from pc_manager_agent.rollback.manager import RollbackManager
from pc_manager_agent.safety.cleanup_candidate_policy import CleanupCandidatePolicy
from pc_manager_agent.safety.file_analysis_validator import FileAnalysisSafetyValidator
from pc_manager_agent.safety.file_operation_validator import FileOperationSafetyValidator
from pc_manager_agent.safety.machine_msi_policy import MachineMsiExecutionPolicy
from pc_manager_agent.safety.machine_startup_policy import MachineStartupSafetyPolicy
from pc_manager_agent.safety.operation_preview import OperationPreviewEngine
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.safety.plan_reviewer import SafetyReviewer
from pc_manager_agent.safety.process_policy import ProcessSafetyPolicy
from pc_manager_agent.safety.process_preview import ProcessPreviewEngine
from pc_manager_agent.safety.process_validator import ProcessActionSafetyValidator
from pc_manager_agent.safety.recycle_bin_empty import RecycleBinEmptyPlanBuilder
from pc_manager_agent.safety.residual_classification import ResidualClassifier
from pc_manager_agent.safety.residual_cleanup_policy import (
    CleanupEligibilityPolicy,
    CleanupRiskPolicy,
    ResidualCleanupPathPolicy,
    ResidualRecentModificationPolicy,
)
from pc_manager_agent.safety.residual_cleanup_preview import ResidualCleanupPreviewEngine
from pc_manager_agent.safety.residual_cleanup_revalidation import FreshResidualRevalidator
from pc_manager_agent.safety.residual_cleanup_validator import (
    ResidualCleanupSafetyValidator,
)
from pc_manager_agent.safety.residual_ownership import ResidualOwnershipEvaluator
from pc_manager_agent.safety.residual_scope_policy import ResidualScanScopePolicy
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.safety.service_preview import ServicePreviewEngine
from pc_manager_agent.safety.service_startup_policy import ServiceStartupSafetyPolicy
from pc_manager_agent.safety.service_startup_preview import ServiceStartupPreviewEngine
from pc_manager_agent.safety.service_startup_validator import ServiceStartupSafetyValidator
from pc_manager_agent.safety.service_validator import ServiceActionSafetyValidator
from pc_manager_agent.safety.software_uninstall_execution_policy import (
    SoftwareUninstallExecutionPolicy,
)
from pc_manager_agent.safety.software_uninstall_execution_preview import (
    MsiUninstallPreviewEngine,
)
from pc_manager_agent.safety.software_uninstall_execution_validator import (
    MsiUninstallSafetyValidator,
)
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.safety.software_uninstall_preview import SoftwareUninstallPreviewEngine
from pc_manager_agent.safety.software_uninstall_validator import (
    SoftwareUninstallSafetyValidator,
)
from pc_manager_agent.safety.software_zero_execution import SoftwareZeroExecutionGuard
from pc_manager_agent.safety.startup_policy import StartupSafetyPolicy
from pc_manager_agent.safety.startup_preview import StartupPreviewEngine
from pc_manager_agent.safety.startup_validator import StartupActionSafetyValidator
from pc_manager_agent.safety.system_cleanup_policy import (
    CleanupRecentActivityPolicy,
    SystemCleanupEligibilityPolicy,
    SystemCleanupPathPolicy,
    SystemCleanupRiskPolicy,
)
from pc_manager_agent.safety.system_cleanup_preview import CleanupExecutionPlanBuilder
from pc_manager_agent.safety.system_cleanup_revalidation import (
    FreshCleanupCandidateRevalidator,
)
from pc_manager_agent.safety.system_cleanup_validator import SystemCleanupSafetyValidator
from pc_manager_agent.safety.system_diagnostics import DiagnosticSafetyValidator
from pc_manager_agent.safety.system_optimization import SystemOptimizationSafetyValidator
from pc_manager_agent.safety.trash_policy import TrashPathPolicy
from pc_manager_agent.safety.trash_preview import TrashPreviewEngine
from pc_manager_agent.safety.trash_validator import TrashSafetyValidator
from pc_manager_agent.safety.user_data_protection import UserDataProtectionPolicy
from pc_manager_agent.safety.vendor_argument_policy import VendorArgumentPolicy
from pc_manager_agent.safety.vendor_executable_trust import VendorExecutableTrustValidator
from pc_manager_agent.safety.vendor_uninstall_policy import VendorUninstallExecutionPolicy
from pc_manager_agent.safety.vendor_uninstall_preview import VendorUninstallPreviewEngine
from pc_manager_agent.safety.vendor_uninstall_validator import VendorUninstallSafetyValidator
from pc_manager_agent.safety.winget_capability_policy import WingetCapabilityPolicy
from pc_manager_agent.safety.winget_uninstall_policy import WingetUninstallPolicy
from pc_manager_agent.safety.winget_uninstall_preview import WingetUninstallPreviewEngine
from pc_manager_agent.safety.winget_uninstall_validator import WingetUninstallSafetyValidator
from pc_manager_agent.tools.file_tools.create_directory import CreateDirectoryTool
from pc_manager_agent.tools.file_tools.duplicate_analyzer import DuplicateFileAnalyzer
from pc_manager_agent.tools.file_tools.hashing import SafeFileHasher
from pc_manager_agent.tools.file_tools.inactive_file_analyzer import InactiveFileAnalyzer
from pc_manager_agent.tools.file_tools.large_file_analyzer import LargeFileAnalyzer
from pc_manager_agent.tools.file_tools.move import MoveTool
from pc_manager_agent.tools.file_tools.remove_created_directory import (
    RemoveCreatedDirectoryTool,
)
from pc_manager_agent.tools.file_tools.rename import RenameTool
from pc_manager_agent.tools.file_tools.scanner import DirectoryScannerTool
from pc_manager_agent.tools.file_tools.trash import TrashTool
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.collectors import (
    CpuTool,
    DiskTool,
    MemoryTool,
    ProcessTool,
    ServiceTool,
    SoftwareTool,
    StartupTool,
    SystemInfoTool,
)
from pc_manager_agent.tools.system_tools.msix_uninstall import MsixUninstallTool
from pc_manager_agent.tools.system_tools.process_actions import (
    ForceTerminateProcessTool,
    RequestProcessExitTool,
)
from pc_manager_agent.tools.system_tools.residual_cleanup import (
    SoftwareResidualPrepareCleanupTool,
    SoftwareResidualTrashTool,
)
from pc_manager_agent.tools.system_tools.service_actions import (
    StartServiceTool,
    StopServiceTool,
)
from pc_manager_agent.tools.system_tools.service_startup_actions import (
    RestoreServiceStartupTool,
    SetServiceAutomaticTool,
    SetServiceManualTool,
)
from pc_manager_agent.tools.system_tools.software_analysis import (
    SoftwareInspectTool,
    SoftwareInventoryTool,
    SoftwareResolveTool,
    SoftwareUninstallCapabilityTool,
    SoftwareUninstallPreviewTool,
)
from pc_manager_agent.tools.system_tools.software_residuals import (
    SoftwareResidualAnalyzeTool,
    SoftwareResidualInspectTool,
    SoftwareResidualReportTool,
)
from pc_manager_agent.tools.system_tools.software_uninstall import MsiUninstallTool
from pc_manager_agent.tools.system_tools.startup_actions import (
    DisableStartupTool,
    RestoreStartupTool,
)
from pc_manager_agent.tools.system_tools.system_cleanup import (
    OptimizationCleanupPrepareTool,
    OptimizationCleanupTrashTool,
    OptimizationRecycleBinEmptyTool,
    OptimizationRecycleBinInspectTool,
)
from pc_manager_agent.tools.system_tools.system_optimization import (
    OptimizationCleanupCandidateTool,
    OptimizationPerformanceTool,
    OptimizationRecommendationTool,
    OptimizationSnapshotTool,
    OptimizationStorageAnalysisTool,
)
from pc_manager_agent.tools.system_tools.vendor_uninstall import VendorUninstallTool
from pc_manager_agent.tools.system_tools.winget_uninstall import WingetUninstallTool


class ProviderConfigurationError(RuntimeError):
    """Raised when a requested model provider lacks explicit safe configuration."""


@dataclass(frozen=True, slots=True)
class FileAnalysisServices:
    """One dependency bundle shared by the Stage 1 UI and headless tests."""

    registry: ToolRegistry
    compiler: FileAnalysisPlanCompiler
    orchestrator: FileAnalysisOrchestrator
    planner: FileAnalysisPlanner | None
    explainer: FileAnalysisExplainer | None


@dataclass(frozen=True, slots=True)
class FileOperationServices:
    """Dependency bundle for the Stage 2A Preview, execution, and rollback workflow."""

    registry: ToolRegistry
    compiler: FileOperationPlanCompiler
    source_resolver: FileOperationSourceResolver
    service: FileOperationService
    rollback: RollbackManager
    planner: FileOperationPlanner | None


@dataclass(frozen=True, slots=True)
class TrashServices:
    """Dependency bundle for deterministic Stage 2B planning and R2 execution."""

    registry: ToolRegistry
    compiler: TrashPlanCompiler
    service: TrashService


@dataclass(frozen=True, slots=True)
class SystemDiagnosticServices:
    """Dependency bundle for confirmed Stage 3 R0 diagnostics."""

    registry: ToolRegistry
    compiler: DiagnosticPlanCompiler
    orchestrator: DiagnosticOrchestrator
    provider_planner: DiagnosticProviderPlanner | None
    explainer: DiagnosticExplainer | None


@dataclass(frozen=True, slots=True)
class SystemOptimizationServices:
    """Isolated five-tool Stage 4E1 bundle with no write-tool or Broker dependency."""

    registry: ToolRegistry
    compiler: SystemOptimizationPlanCompiler
    orchestrator: SystemOptimizationOrchestrator
    exporter: SystemOptimizationReportExporter


@dataclass(frozen=True, slots=True)
class SystemCleanupServices:
    """Isolated Stage 4E2 registry, durable repository, and controlled service."""

    registry: ToolRegistry
    repository: SystemCleanupRepository
    service: SystemCleanupService


@dataclass(frozen=True, slots=True)
class SoftwareAnalysisServices:
    """Dependency bundle for Stage 4D1 zero-execution software analysis."""

    registry: ToolRegistry
    inventory: SoftwareInventoryService
    resolver: SoftwareTargetResolver
    service: SoftwareUninstallAnalysisService


@dataclass(frozen=True, slots=True)
class MsiUninstallServices:
    """Dependency bundle for the single-product Stage 4D2A MSI workflow."""

    registry: ToolRegistry
    resolver: SoftwareTargetResolver
    service: MsiUninstallService


@dataclass(frozen=True, slots=True)
class VendorUninstallServices:
    """Dependency bundle for one trusted interactive Stage 4D2B workflow."""

    registry: ToolRegistry
    resolver: SoftwareTargetResolver
    service: VendorUninstallService


@dataclass(frozen=True, slots=True)
class WingetUninstallServices:
    """Dependency bundle for one trusted Stage 4D2C1 package workflow."""

    registry: ToolRegistry
    software_resolver: SoftwareTargetResolver
    package_resolver: PackageTargetResolver
    service: WingetUninstallService


@dataclass(frozen=True, slots=True)
class MsixUninstallServices:
    """Dependency bundle for the Stage 4D2C2 current-user WinRT workflow."""

    registry: ToolRegistry
    service: MsixUninstallService


@dataclass(frozen=True, slots=True)
class ResidualAnalysisServices:
    """Dependency bundle for Stage 4D3 report-only residual analysis."""

    registry: ToolRegistry
    repository: SoftwareResidualRepository
    service: ResidualAnalysisService
    exporter: ResidualReportExporter
    explorer: WindowsResidualExplorerService


@dataclass(frozen=True, slots=True)
class ResidualCleanupServices:
    """Dependency bundle for fresh Stage 4D4 planning and controlled recycling."""

    registry: ToolRegistry
    repository: ResidualCleanupRepository
    service: ResidualCleanupService


@dataclass(frozen=True, slots=True)
class ProcessActionServices:
    """Dependency bundle for controlled Stage 4A process actions."""

    registry: ToolRegistry
    platform: ProcessManagementPlatform
    resolver: ProcessTargetResolver
    compiler: ProcessActionPlanCompiler
    service: ProcessActionService


@dataclass(frozen=True, slots=True)
class StartupActionServices:
    """Dependency bundle for backed-up Stage 4B startup actions."""

    registry: ToolRegistry
    platform: StartupManagementPlatform
    resolver: StartupTargetResolver
    service: StartupActionService


@dataclass(frozen=True, slots=True)
class ServiceActionServices:
    """Dependency bundle for controlled Stage 4C1 service actions."""

    registry: ToolRegistry
    platform: ServiceControlPlatform
    resolver: ServiceTargetResolver
    compiler: ServiceActionPlanCompiler
    service: ServiceActionService


@dataclass(frozen=True, slots=True)
class ServiceStartupActionServices:
    """Dependency bundle for backed-up Stage 4C2 startup configuration actions."""

    registry: ToolRegistry
    platform: ServiceStartupPlatform
    resolver: ServiceTargetResolver
    service: ServiceStartupActionService


@dataclass(frozen=True, slots=True)
class PrivilegedActionServices:
    """Developer-only Stage 4X1 Mock components; none can elevate Windows."""

    service: PrivilegedActionService
    broker: MockPrivilegedBroker
    caller: PrivilegedCallerContext
    fake_state: FakePrivilegedSystemState


@dataclass(frozen=True, slots=True)
class WindowsPrivilegedActionServices:
    """Real Stage 4X2/4X3 preparation and one-shot Windows Broker coordination."""

    service: PrivilegedActionService
    coordinator: ElevatedServiceActionCoordinator
    caller: PrivilegedCallerContext
    availability: PrivilegedBrokerAvailabilityService


@dataclass(frozen=True, slots=True)
class Stage4X3ActionServices:
    """Dedicated Main preparation for service config, HKLM Run, and machine MSI."""

    preparation: ElevatedStage4X3PreparationService


class ApplicationRuntime:
    """Own shared infrastructure and create root-scoped orchestrators."""

    def __init__(self, settings: AppSettings) -> None:
        """保存配置并初始化审计、确认、授权目录和分析结果等共享服务。"""
        self.settings = settings
        self.audit = AuditRepository(settings.database_path)
        self.audit.initialize()
        self.optimization_session_repository = OptimizationSessionRepository(settings.database_path)
        self.optimization_session_repository.initialize()
        self.optimization_result_reader = OptimizationDomainResultReader(settings.database_path)
        self._optimization_reviews: OptimizationReviewServices | None = None
        self.agent_instance_id = uuid4()
        self.privileged_action_repository = PrivilegedActionRepository(settings.database_path)
        self.interrupted_privileged_action_ids = self.privileged_action_repository.initialize()
        self.confirmation = ConfirmationService(settings.confirmation_ttl_seconds)
        self.external_consent = ExternalDataConsentService(
            settings.confirmation_ttl_seconds,
            on_resolved=self._audit_external_consent,
        )
        self.authorized_path_repository = AuthorizedPathRepository(settings.database_path)
        self.authorized_path_repository.initialize()
        self.authorized_paths = AuthorizedPathService(
            self.authorized_path_repository,
            network_path_detector=is_network_path,
            on_change=self._audit_authorized_path_change,
        )
        self.analysis_results = AnalysisResultRepository(settings.database_path)
        self.analysis_results.initialize()
        self.operation_repository = OperationRepository(settings.database_path)
        self.interrupted_operation_ids = self.operation_repository.initialize()
        self.operation_confirmation = OperationConfirmationService(
            settings.confirmation_ttl_seconds
        )
        self.rollback_confirmation = RollbackConfirmationService(settings.confirmation_ttl_seconds)
        self.trash_confirmation = TrashConfirmationService(
            settings.confirmation_ttl_seconds,
            settings.trash_runtime_confirmation_ttl_seconds,
        )
        self.diagnostic_confirmation = DiagnosticConfirmationService(
            settings.confirmation_ttl_seconds
        )
        self.optimization_confirmation = OptimizationConfirmationService(
            settings.confirmation_ttl_seconds
        )
        self.optimization_report_store = OptimizationReportSessionStore(
            ttl_seconds=settings.system_cleanup_report_ttl_seconds
        )
        self.process_confirmation = ProcessActionConfirmationService(
            settings.confirmation_ttl_seconds,
            settings.process_runtime_confirmation_ttl_seconds,
        )
        self.process_action_repository = ProcessActionRepository(settings.database_path)
        self.interrupted_process_action_ids = self.process_action_repository.initialize()
        self.startup_confirmation = StartupActionConfirmationService(
            settings.confirmation_ttl_seconds,
            settings.startup_runtime_confirmation_ttl_seconds,
        )
        self.startup_action_repository = StartupActionRepository(settings.database_path)
        self.interrupted_startup_action_ids = self.startup_action_repository.initialize()
        self.startup_backup_vault = StartupBackupVault(
            settings.database_path,
            WindowsCurrentUserDataProtector(),
        )
        self.startup_backup_vault.initialize()
        self.service_confirmation = ServiceActionConfirmationService(
            settings.confirmation_ttl_seconds,
            settings.service_runtime_confirmation_ttl_seconds,
        )
        self.service_action_repository = ServiceActionRepository(settings.database_path)
        self.interrupted_service_action_ids = self.service_action_repository.initialize()
        self.service_startup_confirmation = ServiceStartupActionConfirmationService(
            settings.confirmation_ttl_seconds,
            settings.service_runtime_confirmation_ttl_seconds,
        )
        self.service_startup_repository = ServiceStartupActionRepository(settings.database_path)
        self.interrupted_service_startup_ids = self.service_startup_repository.initialize()
        self.service_startup_backup_vault = ServiceStartupBackupVault(
            settings.database_path,
            WindowsCurrentUserDataProtector(),
        )
        self.service_startup_backup_vault.initialize()
        self.msi_uninstall_repository = MsiUninstallRepository(settings.database_path)
        self.interrupted_msi_uninstall_ids = self.msi_uninstall_repository.initialize()
        self.vendor_uninstall_repository = VendorUninstallRepository(settings.database_path)
        self.interrupted_vendor_uninstall_ids = self.vendor_uninstall_repository.initialize()
        self.winget_uninstall_repository = WingetUninstallRepository(settings.database_path)
        self.interrupted_winget_uninstall_ids = self.winget_uninstall_repository.initialize()
        self.msix_uninstall_repository = MsixUninstallRepository(settings.database_path)
        self.interrupted_msix_uninstall_ids = self.msix_uninstall_repository.initialize()
        self.software_residual_repository = SoftwareResidualRepository(settings.database_path)
        self.software_residual_repository.initialize()
        self.residual_cleanup_repository = ResidualCleanupRepository(settings.database_path)
        self.interrupted_residual_cleanup_ids = self.residual_cleanup_repository.initialize()
        self.system_cleanup_repository = SystemCleanupRepository(settings.database_path)
        self.interrupted_system_cleanup_ids = self.system_cleanup_repository.initialize()
        self.uninstall_context_recorder = UninstallContextRecorder(
            self.software_residual_repository
        )
        self.residual_report_exporter = ResidualReportExporter()
        self.residual_explorer = WindowsResidualExplorerService()
        self.file_operation_platform = WindowsFileOperationPlatform()
        self.recycle_bin_platform = WindowsRecycleBinPlatform()
        self.recycle_bin_empty_platform = WindowsRecycleBinEmptyPlatform(self.recycle_bin_platform)
        self.cleanup_activity_probe = WindowsCleanupActivityProbe()
        self.process_management_platform = WindowsProcessManagementPlatform()
        self.startup_management_platform = WindowsStartupManagementPlatform(
            settings.data_directory / "disabled_startup"
        )
        self.service_control_platform = WindowsServiceControlPlatform()
        self.service_startup_platform = WindowsServiceStartupPlatform()
        self.report_exporter = ReportExporter(
            self.analysis_results,
            on_export=self._audit_report_export,
        )
        self.explorer = WindowsExplorerService(self.authorized_paths)
        self.office: OfficeServices = build_office_services(
            settings, self.audit, self.authorized_paths.forbidden_roots
        )
        self.browser: BrowserServices = build_browser_services(settings, self.audit)
        self.agents: AgentServices = build_agent_services(settings, self.audit)
        self.tasks: FinalTaskServices = build_final_task_services(settings, self.audit)

    def require_feature(self, feature: ReleaseFeature) -> None:
        """Fail before preparation when the immutable build policy disables a domain."""
        self.settings.feature_flags.require(feature)

    def create_scan_orchestrator(self, root: Path) -> ScanOrchestrator:
        """为一个用户选择的根目录创建独立路径策略、注册表和扫描编排器。"""
        self.require_feature(ReleaseFeature.FILE_ANALYSIS)
        policy = PathPolicy.for_scan_root(root)
        registry = ToolRegistry()
        registry.register(DirectoryScannerTool(policy))
        reviewer = SafetyReviewer(registry, policy)
        return ScanOrchestrator(
            registry=registry,
            reviewer=reviewer,
            path_policy=policy,
            confirmation=self.confirmation,
            audit=self.audit,
            max_files=self.settings.scan_max_files,
            timeout_seconds=self.settings.scan_timeout_seconds,
        )

    def create_llm_provider(self) -> LLMProvider | None:
        """Build an explicitly configured provider; disabled is the safe default."""
        if self.settings.llm_provider == "disabled":
            return None
        if self.settings.llm_provider == "openai":
            if self.settings.openai_model is None or self.settings.openai_api_key is None:
                raise ProviderConfigurationError(
                    "OpenAI requires OPENAI_MODEL and OPENAI_API_KEY environment variables"
                )
            return OpenAILLMProvider(
                model=self.settings.openai_model,
                api_key=self.settings.openai_api_key.get_secret_value(),
            )
        raise ProviderConfigurationError("Unsupported LLM provider")

    def create_file_analysis_services(
        self,
        progress_callback: Callable[[FileAnalysisProgress], None] | None = None,
    ) -> FileAnalysisServices:
        """Build Stage 1 services over exactly the currently authorized roots."""
        self.require_feature(ReleaseFeature.FILE_ANALYSIS)
        records = self.authorized_paths.list_authorized()
        if not records:
            raise ValueError("Add at least one authorized directory before analysis")
        root_ids = tuple(record.path_id for record in records)
        policy = self.authorized_paths.build_policy(root_ids)

        def scanner_progress(value: ScanProgress) -> None:
            if progress_callback is None or value.session_id is None:
                return
            progress_callback(
                FileAnalysisProgress(
                    analysis_session_id=value.session_id,
                    phase=value.phase,
                    files_scanned=value.files_seen,
                    directories_scanned=value.directories_seen,
                    total_bytes=value.total_size_bytes,
                    errors=value.issues,
                    completed_units=value.completed_units,
                    total_units=value.total_units,
                )
            )

        def analyzer_progress(
            session_id: UUID,
            phase: str,
            completed: int,
            total: int | None,
        ) -> None:
            if progress_callback is None:
                return
            progress_callback(
                FileAnalysisProgress(
                    analysis_session_id=session_id,
                    phase=phase,
                    files_scanned=0,
                    directories_scanned=0,
                    total_bytes=0,
                    completed_units=completed,
                    total_units=total,
                )
            )

        registry = ToolRegistry()
        registry.register(
            DirectoryScannerTool(
                policy,
                batch_consumer=self.analysis_results.store_batch,
                issue_consumer=self.analysis_results.store_issues,
                progress_callback=scanner_progress,
            )
        )
        registry.register(
            LargeFileAnalyzer(
                self.analysis_results,
                progress_callback=analyzer_progress,
            )
        )
        registry.register(
            InactiveFileAnalyzer(
                self.analysis_results,
                atime_reliability=last_access_time_reliable,
                progress_callback=analyzer_progress,
            )
        )
        registry.register(
            DuplicateFileAnalyzer(
                self.analysis_results,
                SafeFileHasher(policy),
                progress_callback=analyzer_progress,
            )
        )
        reviewer = SafetyReviewer(registry, policy)
        validator = FileAnalysisSafetyValidator(
            reviewer,
            registry,
            self.authorized_paths,
        )
        compiler = FileAnalysisPlanCompiler(
            self.authorized_paths,
            registry,
            max_files=self.settings.scan_max_files,
            timeout_seconds=self.settings.scan_timeout_seconds,
            batch_size=self.settings.analysis_batch_size,
        )
        orchestrator = FileAnalysisOrchestrator(
            registry=registry,
            validator=validator,
            confirmation=self.confirmation,
            audit=self.audit,
            results=self.analysis_results,
        )
        provider = self.create_llm_provider()
        planner = None
        explainer = None
        if provider is not None:
            planner = FileAnalysisPlanner(
                provider,
                self.authorized_paths,
                registry,
                compiler,
                self.external_consent,
            )
            explainer = FileAnalysisExplainer(provider, self.external_consent)
        return FileAnalysisServices(
            registry=registry,
            compiler=compiler,
            orchestrator=orchestrator,
            planner=planner,
            explainer=explainer,
        )

    def create_file_operation_services(
        self,
        progress_callback: Callable[[OperationProgress], None] | None = None,
    ) -> FileOperationServices:
        """Build Stage 2A services over exactly the currently authorized roots."""
        self.require_feature(ReleaseFeature.FILE_OPERATIONS)
        records = self.authorized_paths.list_authorized()
        if not records:
            raise ValueError("Add at least one authorized directory before file operations")
        root_ids = tuple(record.path_id for record in records)
        policy = self.authorized_paths.build_policy(root_ids)
        guard = TransactionExecutionGuard(self.operation_repository)
        registry = ToolRegistry(write_guard=guard)
        registry.register(CreateDirectoryTool(policy, self.file_operation_platform))
        registry.register(MoveTool(policy, self.file_operation_platform))
        registry.register(RenameTool(policy, self.file_operation_platform))
        registry.register(RemoveCreatedDirectoryTool(policy, self.file_operation_platform))
        audit = OperationAuditLogger(
            self.audit,
            app_version=__version__,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        validator = FileOperationSafetyValidator(
            registry,
            policy,
            max_operations=self.settings.operation_max_objects,
        )
        preview = OperationPreviewEngine(
            policy,
            self.file_operation_platform,
            max_objects=self.settings.operation_max_objects,
            max_total_bytes=self.settings.operation_max_total_bytes,
        )
        executor = TransactionExecutor(
            self.operation_repository,
            registry,
            self.operation_confirmation,
            audit,
            progress_callback=progress_callback,
        )
        service = FileOperationService(
            validator,
            preview,
            self.operation_confirmation,
            self.operation_repository,
            executor,
            audit,
        )
        provider = self.create_llm_provider()
        planner = (
            FileOperationPlanner(provider, self.authorized_paths, registry, self.external_consent)
            if provider is not None
            else None
        )
        rollback = RollbackManager(
            self.operation_repository,
            policy,
            self.file_operation_platform,
            registry,
            self.rollback_confirmation,
            audit,
        )
        return FileOperationServices(
            registry=registry,
            compiler=FileOperationPlanCompiler(
                self.authorized_paths,
                self.file_operation_platform,
                max_operations=self.settings.operation_max_objects,
            ),
            source_resolver=FileOperationSourceResolver(
                self.authorized_paths,
                max_sources=self.settings.operation_max_objects,
            ),
            service=service,
            rollback=rollback,
            planner=planner,
        )

    def create_trash_services(
        self,
        _progress_callback: Callable[[TrashExecutionReport], None] | None = None,
    ) -> TrashServices:
        """Build Stage 2B services without granting a model any path-selection authority."""
        self.require_feature(ReleaseFeature.RECYCLE_BIN)
        records = self.authorized_paths.list_authorized()
        if not records:
            raise ValueError("Add at least one authorized directory before Recycle Bin operations")
        root_ids = tuple(record.path_id for record in records)
        base_policy = self.authorized_paths.build_policy(root_ids)
        policy = TrashPathPolicy(base_policy)
        guard = TransactionExecutionGuard(self.operation_repository)
        registry = ToolRegistry(write_guard=guard)
        preview = TrashPreviewEngine(
            policy,
            self.file_operation_platform,
            self.recycle_bin_platform,
            max_selected=self.settings.trash_max_selected,
            max_contained_objects=self.settings.trash_max_contained_objects,
            max_total_bytes=self.settings.trash_max_total_bytes,
            high_impact_objects=self.settings.trash_high_impact_objects,
            high_impact_bytes=self.settings.trash_high_impact_bytes,
        )
        registry.register(
            TrashTool(
                policy,
                self.file_operation_platform,
                self.recycle_bin_platform,
                preview.snapshot,
            )
        )
        validator = TrashSafetyValidator(
            registry,
            policy,
            max_selected=self.settings.trash_max_selected,
        )
        audit = TrashAuditLogger(
            self.audit,
            app_version=__version__,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        service = TrashService(
            validator,
            preview,
            self.trash_confirmation,
            self.operation_repository,
            registry,
            audit,
        )
        return TrashServices(
            registry=registry,
            compiler=TrashPlanCompiler(
                self.authorized_paths,
                policy,
                self.file_operation_platform,
                max_selected=self.settings.trash_max_selected,
            ),
            service=service,
        )

    def audit_prohibited_request(self, original_request: str, reason: str) -> None:
        """Record a local R4 refusal without invoking a model or registered tool."""
        self.audit.record(
            AuditEvent(
                event_type="trash.request_refused",
                original_request=original_request,
                agent_decision=reason,
                risk_level=RiskLevel.R4,
                confirmation_required=False,
                result={"executed": False, "tool_registered": False},
                app_version=__version__,
                git_commit=os.getenv("GITHUB_SHA"),
            )
        )

    def create_system_diagnostic_services(self) -> SystemDiagnosticServices:
        """Build all eight read-only collectors behind one confirmed orchestrator."""
        self.require_feature(ReleaseFeature.SYSTEM_DIAGNOSTICS)
        platform_adapter = WindowsSystemDiagnosticsPlatform()
        registry = ToolRegistry()
        for tool in (
            SystemInfoTool(platform_adapter),
            CpuTool(platform_adapter),
            MemoryTool(platform_adapter),
            DiskTool(platform_adapter),
            ProcessTool(platform_adapter),
            StartupTool(platform_adapter),
            ServiceTool(platform_adapter),
            SoftwareTool(platform_adapter),
        ):
            registry.register(tool)
        compiler = DiagnosticPlanCompiler(
            registry,
            sample_count=self.settings.diagnostic_sample_count,
            sample_interval_seconds=self.settings.diagnostic_sample_interval_seconds,
            max_processes=self.settings.diagnostic_max_processes,
            max_items=self.settings.diagnostic_max_items,
        )
        safety = DiagnosticSafetyValidator(registry)
        audit = DiagnosticAuditLogger(
            self.audit,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        orchestrator = DiagnosticOrchestrator(
            compiler=compiler,
            safety=safety,
            confirmation=self.diagnostic_confirmation,
            snapshot_service=SystemSnapshotService(registry, audit),
            engine=DiagnosticEngine(),
            audit=audit,
        )
        provider = self.create_llm_provider()
        return SystemDiagnosticServices(
            registry=registry,
            compiler=compiler,
            orchestrator=orchestrator,
            provider_planner=(
                DiagnosticProviderPlanner(provider, compiler, self.external_consent)
                if provider is not None
                else None
            ),
            explainer=(
                DiagnosticExplainer(provider, self.external_consent)
                if provider is not None
                else None
            ),
        )

    def create_system_optimization_services(self) -> SystemOptimizationServices:
        """Build the exact Stage 4E1 R0 graph without importing any mutation adapter."""
        self.require_feature(ReleaseFeature.OPTIMIZATION_ANALYSIS)
        platform_adapter = WindowsSystemOptimizationPlatform()
        cleanup_policy = CleanupCandidatePolicy()
        performance_engine = PerformanceDiagnosticEngine()
        recommendation_engine = OptimizationRecommendationEngine()
        registry = ToolRegistry()
        for tool in (
            OptimizationSnapshotTool(platform_adapter),
            OptimizationStorageAnalysisTool(
                platform_adapter,
                RepositoryOptimizationEvidenceSource(
                    self.analysis_results,
                    self.software_residual_repository,
                ),
            ),
            OptimizationCleanupCandidateTool(cleanup_policy),
            OptimizationPerformanceTool(performance_engine),
            OptimizationRecommendationTool(recommendation_engine),
        ):
            registry.register(tool)
        compiler = SystemOptimizationPlanCompiler(
            self.authorized_paths,
            max_objects=self.settings.optimization_max_objects,
            timeout_seconds=self.settings.optimization_timeout_seconds,
            minimum_large_file_bytes=self.settings.optimization_large_file_bytes,
            inactive_days=self.settings.optimization_inactive_days,
            sample_count=self.settings.optimization_sample_count,
            sample_interval_seconds=self.settings.optimization_sample_interval_seconds,
        )
        audit = SystemOptimizationAuditLogger(
            self.audit,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        orchestrator = SystemOptimizationOrchestrator(
            registry=registry,
            compiler=compiler,
            safety=SystemOptimizationSafetyValidator(registry, self.authorized_paths),
            confirmation=self.optimization_confirmation,
            audit=audit,
            report_store=self.optimization_report_store,
        )
        return SystemOptimizationServices(
            registry=registry,
            compiler=compiler,
            orchestrator=orchestrator,
            exporter=SystemOptimizationReportExporter(),
        )

    def create_optimization_review_services(self) -> OptimizationReviewServices:
        """Reuse one finite Stage 4E3 review container; it holds no write authority."""
        self.require_feature(ReleaseFeature.OPTIMIZATION_ANALYSIS)
        if self._optimization_reviews is None:
            self._optimization_reviews = build_optimization_review_services(self)
        return self._optimization_reviews

    def create_system_cleanup_services(self) -> SystemCleanupServices:
        """Build the isolated Stage 4E2 Fresh-check and Recycle Bin workflow."""
        self.require_feature(ReleaseFeature.SYSTEM_CLEANUP)
        local_app_data = Path(
            os.environ.get(
                "LOCALAPPDATA",
                str(Path.home() / "AppData" / "Local"),
            )
        )
        known_roots = {
            "current-user-temp": local_app_data / "Temp",
            "directx-shader-cache": local_app_data / "D3DSCache",
            "current-user-crash-dumps": local_app_data / "CrashDumps",
        }
        path_policy = SystemCleanupPathPolicy(
            known_roots,
            network_path_detector=is_network_path,
        )
        revalidator = FreshCleanupCandidateRevalidator(
            self.optimization_report_store,
            path_policy,
            SystemCleanupEligibilityPolicy(),
            CleanupRecentActivityPolicy(
                minimum_age_days=self.settings.system_cleanup_minimum_age_days
            ),
            self.file_operation_platform,
            self.recycle_bin_platform,
            self.cleanup_activity_probe,
            active_installer=lambda: any(
                (
                    self.msi_uninstall_repository.has_active_uninstall(),
                    self.vendor_uninstall_repository.has_active_uninstall(),
                    self.winget_uninstall_repository.has_active_uninstall(),
                    self.msix_uninstall_repository.has_active_uninstall(),
                )
            ),
            max_selected_candidates=self.settings.system_cleanup_max_selected,
            max_discovered_items=self.settings.system_cleanup_max_discovered,
            max_contained_objects=self.settings.system_cleanup_max_contained_objects,
            max_total_bytes=self.settings.system_cleanup_max_total_bytes,
        )
        plan_builder = CleanupExecutionPlanBuilder(
            revalidator,
            SystemCleanupRiskPolicy(
                max_normal_items=self.settings.system_cleanup_normal_item_count,
                max_normal_objects=self.settings.system_cleanup_normal_object_count,
                max_normal_total_bytes=self.settings.system_cleanup_normal_total_bytes,
                max_normal_single_item_bytes=(
                    self.settings.system_cleanup_normal_single_item_bytes
                ),
            ),
            max_selected_items=self.settings.system_cleanup_max_selected,
            plan_ttl_seconds=self.settings.confirmation_ttl_seconds,
            preview_ttl_seconds=self.settings.system_cleanup_runtime_confirmation_ttl_seconds,
        )
        empty_plan_builder = RecycleBinEmptyPlanBuilder(
            self.recycle_bin_empty_platform,
            plan_ttl_seconds=self.settings.confirmation_ttl_seconds,
            preview_ttl_seconds=self.settings.system_cleanup_runtime_confirmation_ttl_seconds,
        )
        guard = SystemCleanupExecutionGuard(self.system_cleanup_repository)
        registry = ToolRegistry(write_guard=guard)
        for tool in (
            OptimizationCleanupPrepareTool(revalidator),
            OptimizationCleanupTrashTool(
                self.system_cleanup_repository,
                revalidator,
                self.file_operation_platform,
                self.recycle_bin_platform,
            ),
            OptimizationRecycleBinInspectTool(self.recycle_bin_empty_platform),
            OptimizationRecycleBinEmptyTool(
                self.system_cleanup_repository,
                self.recycle_bin_empty_platform,
            ),
        ):
            registry.register(tool)
        confirmation = SystemCleanupConfirmationService(
            self.system_cleanup_repository,
            plan_builder,
            plan_ttl_seconds=self.settings.confirmation_ttl_seconds,
            runtime_ttl_seconds=self.settings.system_cleanup_runtime_confirmation_ttl_seconds,
        )
        audit = SystemCleanupAuditLogger(
            self.audit,
            app_version=__version__,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        service = SystemCleanupService(
            registry,
            plan_builder,
            empty_plan_builder,
            SystemCleanupSafetyValidator(registry),
            confirmation,
            self.system_cleanup_repository,
            self.file_operation_platform,
            audit,
        )
        return SystemCleanupServices(
            registry=registry,
            repository=self.system_cleanup_repository,
            service=service,
        )

    def create_software_analysis_services(self) -> SoftwareAnalysisServices:
        """Build the exact five-tool R0 Stage 4D1 workflow with no execution registry."""
        self.require_feature(ReleaseFeature.SOFTWARE_ANALYSIS)
        inventory = SoftwareInventoryService(WindowsSoftwareInventoryPlatform())
        resolver = SoftwareTargetResolver(inventory)
        capability = UninstallCapabilityResolver()
        policy = SoftwareUninstallSafetyPolicy(agent_root=Path(__file__).resolve().parents[1])
        preview = SoftwareUninstallPreviewEngine(
            policy,
            capability,
            SoftwareImpactAnalyzer(
                WindowsSystemDiagnosticsPlatform(),
                max_items=self.settings.diagnostic_max_items,
            ),
            ttl_seconds=self.settings.confirmation_ttl_seconds,
        )
        registry = ToolRegistry()
        for tool in (
            SoftwareInventoryTool(inventory),
            SoftwareResolveTool(resolver),
            SoftwareInspectTool(resolver),
            SoftwareUninstallCapabilityTool(resolver, capability),
            SoftwareUninstallPreviewTool(resolver, preview),
        ):
            registry.register(tool)
        guard = SoftwareZeroExecutionGuard()
        guard.validate_registry(registry)
        validator = SoftwareUninstallSafetyValidator(registry, guard)
        audit = SoftwareUninstallAnalysisAuditLogger(
            self.audit,
            app_version=__version__,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        service = SoftwareUninstallAnalysisService(
            SoftwareUninstallAnalysisPlanCompiler(max_items=self.settings.diagnostic_max_items),
            registry,
            validator,
            guard,
            SoftwareAnalysisConfirmationService(
                plan_ttl_seconds=self.settings.confirmation_ttl_seconds,
                acknowledgement_ttl_seconds=self.settings.confirmation_ttl_seconds,
            ),
            audit,
        )
        return SoftwareAnalysisServices(
            registry=registry,
            inventory=inventory,
            resolver=resolver,
            service=service,
        )

    def create_msi_uninstall_services(self) -> MsiUninstallServices:
        """Build the one-tool, current-user MSI uninstall execution boundary."""
        self.require_feature(ReleaseFeature.SOFTWARE_UNINSTALL)
        inventory = SoftwareInventoryService(WindowsSoftwareInventoryPlatform())
        resolver = SoftwareTargetResolver(inventory)
        capability = UninstallCapabilityResolver()
        msi_inventory = WindowsMsiProductInventory()
        platform = WindowsMsiUninstallPlatform(
            poll_interval_seconds=self.settings.msi_monitor_poll_seconds,
            long_running_seconds=self.settings.msi_long_running_seconds,
        )
        registry = ToolRegistry(
            write_guard=MsiUninstallExecutionGuard(self.msi_uninstall_repository)
        )
        registry.register(MsiUninstallTool(platform))
        analysis_policy = SoftwareUninstallSafetyPolicy(
            agent_root=Path(__file__).resolve().parents[1]
        )
        audit = MsiUninstallAuditLogger(
            self.audit,
            app_version=__version__,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        service = MsiUninstallService(
            resolver,
            capability,
            MsiProductValidator(msi_inventory),
            analysis_policy,
            SoftwareUninstallExecutionPolicy(),
            SoftwareExecutionPreflight(
                WindowsSystemDiagnosticsPlatform(),
                max_items=self.settings.diagnostic_max_items,
            ),
            MsiUninstallPreviewEngine(self.settings.confirmation_ttl_seconds),
            MsiUninstallSafetyValidator(registry),
            MsiUninstallConfirmationService(
                self.msi_uninstall_repository,
                plan_ttl_seconds=self.settings.confirmation_ttl_seconds,
                runtime_ttl_seconds=self.settings.msi_runtime_confirmation_ttl_seconds,
            ),
            self.msi_uninstall_repository,
            registry,
            MsiUninstallVerifier(resolver, msi_inventory),
            SoftwareResidualAnalyzer(),
            audit,
            process_is_elevated=current_process_is_elevated,
            max_items=self.settings.diagnostic_max_items,
            context_recorder=self.uninstall_context_recorder,
        )
        return MsiUninstallServices(registry=registry, resolver=resolver, service=service)

    def create_software_uninstall_router(self) -> SoftwareUninstallRouter:
        """Build a read-only fresh-metadata router with no execution authority."""
        self.require_feature(ReleaseFeature.SOFTWARE_UNINSTALL)
        inventory = SoftwareInventoryService(WindowsSoftwareInventoryPlatform())
        return SoftwareUninstallRouter(
            SoftwareTargetResolver(inventory),
            UninstallCapabilityResolver(),
            max_items=self.settings.diagnostic_max_items,
        )

    def create_vendor_uninstall_services(self) -> VendorUninstallServices:
        """Build the one-tool trusted interactive Vendor uninstall boundary."""
        self.require_feature(ReleaseFeature.SOFTWARE_UNINSTALL)
        inventory = SoftwareInventoryService(WindowsSoftwareInventoryPlatform())
        resolver = SoftwareTargetResolver(inventory)
        capability = UninstallCapabilityResolver()
        registry = ToolRegistry(
            write_guard=VendorUninstallExecutionGuard(self.vendor_uninstall_repository)
        )
        registry.register(
            VendorUninstallTool(
                WindowsVendorUninstallPlatform(
                    poll_interval_seconds=self.settings.vendor_monitor_poll_seconds,
                    long_running_seconds=self.settings.vendor_long_running_seconds,
                )
            )
        )
        audit = VendorUninstallAuditLogger(
            self.audit,
            app_version=__version__,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        service = VendorUninstallService(
            resolver,
            capability,
            VendorUninstallMetadataParser(),
            VendorArgumentPolicy(),
            VendorExecutableTrustValidator(WindowsVendorExecutablePlatform()),
            SoftwareUninstallSafetyPolicy(agent_root=Path(__file__).resolve().parents[1]),
            VendorUninstallExecutionPolicy(),
            VendorExecutionPreflight(
                WindowsSystemDiagnosticsPlatform(),
                max_items=self.settings.diagnostic_max_items,
            ),
            VendorUninstallPreviewEngine(self.settings.confirmation_ttl_seconds),
            VendorUninstallSafetyValidator(registry),
            VendorUninstallConfirmationService(
                self.vendor_uninstall_repository,
                plan_ttl_seconds=self.settings.confirmation_ttl_seconds,
                runtime_ttl_seconds=self.settings.vendor_runtime_confirmation_ttl_seconds,
            ),
            self.vendor_uninstall_repository,
            registry,
            VendorUninstallVerifier(resolver),
            VendorResidualAnalyzer(),
            audit,
            process_is_elevated=current_process_is_elevated,
            max_items=self.settings.diagnostic_max_items,
            context_recorder=self.uninstall_context_recorder,
        )
        return VendorUninstallServices(registry=registry, resolver=resolver, service=service)

    def create_winget_uninstall_services(self) -> WingetUninstallServices:
        """Build the one-tool official-source current-user winget boundary."""
        self.require_feature(ReleaseFeature.SOFTWARE_UNINSTALL)
        availability_platform = WindowsWingetAvailabilityPlatform()
        package_inventory = PackageInventoryService(
            WindowsWingetPackageInventoryPlatform(
                availability_platform,
                self.settings.data_directory / "temporary",
            )
        )
        package_resolver = PackageTargetResolver(package_inventory)
        software_inventory = SoftwareInventoryService(
            WindowsSoftwareInventoryPlatform(
                package_provider=IndependentWingetSoftwarePackageProvider()
            )
        )
        software_resolver = SoftwareTargetResolver(software_inventory)
        registry = ToolRegistry(
            write_guard=WingetUninstallExecutionGuard(self.winget_uninstall_repository)
        )
        registry.register(
            WingetUninstallTool(
                WindowsWingetUninstallPlatform(
                    availability_platform,
                    poll_seconds=self.settings.winget_monitor_poll_seconds,
                )
            )
        )
        verifier = WingetUninstallVerifier(package_resolver, software_resolver)
        service = WingetUninstallService(
            package_resolver=package_resolver,
            software_resolver=software_resolver,
            mapper=WingetSoftwareMapper(),
            availability=WingetAvailabilityService(availability_platform),
            capability=WingetCapabilityPolicy(),
            analysis_policy=SoftwareUninstallSafetyPolicy(
                agent_root=Path(__file__).resolve().parents[1]
            ),
            execution_policy=WingetUninstallPolicy(),
            preflight=WingetExecutionPreflightService(
                WindowsSystemDiagnosticsPlatform(),
                max_items=self.settings.diagnostic_max_items,
            ),
            preview_engine=WingetUninstallPreviewEngine(self.settings.confirmation_ttl_seconds),
            validator=WingetUninstallSafetyValidator(),
            repository=self.winget_uninstall_repository,
            confirmations=WingetUninstallConfirmationService(
                self.winget_uninstall_repository,
                plan_ttl_seconds=self.settings.confirmation_ttl_seconds,
                runtime_ttl_seconds=(self.settings.winget_runtime_confirmation_ttl_seconds),
            ),
            registry=registry,
            verifier=verifier,
            residual=WingetResidualAnalyzer(),
            audit=WingetUninstallAuditLogger(
                self.audit,
                app_version=__version__,
                git_commit=os.getenv("GITHUB_SHA"),
            ),
            max_items=self.settings.diagnostic_max_items,
            process_is_elevated=current_process_is_elevated,
            context_recorder=self.uninstall_context_recorder,
        )
        return WingetUninstallServices(
            registry=registry,
            software_resolver=software_resolver,
            package_resolver=package_resolver,
            service=service,
        )

    def create_msix_uninstall_services(self) -> MsixUninstallServices:
        """Build the one-tool current-user MSIX WinRT removal boundary."""
        self.require_feature(ReleaseFeature.SOFTWARE_UNINSTALL)
        platform = WindowsMsixPackagePlatform()
        registry = ToolRegistry(
            write_guard=MsixUninstallExecutionGuard(self.msix_uninstall_repository)
        )
        registry.register(MsixUninstallTool(platform, self.settings.diagnostic_max_items))
        preflight = MsixExecutionPreflightService(
            WindowsSystemDiagnosticsPlatform(), self.settings.diagnostic_max_items
        )
        service = MsixUninstallService(
            platform=platform,
            repository=self.msix_uninstall_repository,
            confirmations=MsixConfirmationService(
                self.msix_uninstall_repository,
                plan_ttl_seconds=self.settings.confirmation_ttl_seconds,
                runtime_ttl_seconds=60,
            ),
            registry=registry,
            audit=MsixUninstallAuditLogger(
                self.audit,
                app_version=__version__,
                git_commit=os.getenv("GITHUB_SHA"),
            ),
            preflight=preflight.inspect,
            process_is_elevated=current_process_is_elevated,
            max_items=self.settings.diagnostic_max_items,
            preview_ttl_seconds=self.settings.confirmation_ttl_seconds,
            context_recorder=self.uninstall_context_recorder,
        )
        return MsixUninstallServices(registry=registry, service=service)

    def create_residual_analysis_services(self) -> ResidualAnalysisServices:
        """Build the fixed three-tool Stage 4D3 read-only analysis boundary."""
        self.require_feature(ReleaseFeature.RESIDUAL_ANALYSIS)
        scope = ResidualScanScopePolicy(
            max_roots=self.settings.residual_max_roots,
            network_path_detector=is_network_path,
        )
        classifier = ResidualClassifier()
        ownership = ResidualOwnershipEvaluator()
        protection = UserDataProtectionPolicy()
        dependencies = (scope, classifier, ownership, protection)
        collectors = (
            InstallLocationResidualCollector(*dependencies),
            KnownAppDataResidualCollector(*dependencies),
            ShortcutResidualCollector(*dependencies),
            MsixDataResidualCollector(*dependencies),
            KnownServiceArtifactCollector(*dependencies),
            KnownConfigurationResidualCollector(*dependencies),
        )
        analyzer = ResidualAnalyzer(
            self.software_residual_repository,
            scope,
            collectors,
        )
        registry = ToolRegistry()
        registry.register(SoftwareResidualAnalyzeTool(analyzer))
        registry.register(SoftwareResidualReportTool(self.software_residual_repository))
        registry.register(SoftwareResidualInspectTool(self.software_residual_repository))
        reviewer = ResidualSafetyReviewer(registry, scope)
        service = ResidualAnalysisService(
            self.software_residual_repository,
            ResidualAnalysisPlanCompiler(
                scope,
                self.settings.residual_max_objects,
                self.settings.residual_timeout_seconds,
            ),
            reviewer,
            self.confirmation,
            registry,
            SoftwareResidualAuditLogger(
                self.audit,
                app_version=__version__,
                git_commit=os.getenv("GITHUB_SHA"),
            ),
        )
        return ResidualAnalysisServices(
            registry=registry,
            repository=self.software_residual_repository,
            service=service,
            exporter=self.residual_report_exporter,
            explorer=self.residual_explorer,
        )

    def create_residual_cleanup_services(self) -> ResidualCleanupServices:
        """Build the independent Stage 4D4 fresh validation and Recycle Bin boundary."""
        self.require_feature(ReleaseFeature.RESIDUAL_CLEANUP)
        scope = ResidualScanScopePolicy(
            max_roots=self.settings.residual_max_roots,
            network_path_detector=is_network_path,
        )
        classifier = ResidualClassifier()
        ownership = ResidualOwnershipEvaluator()
        protection = UserDataProtectionPolicy()
        path_policy = ResidualCleanupPathPolicy(
            scope,
            network_path_detector=is_network_path,
        )
        revalidator = FreshResidualRevalidator(
            self.software_residual_repository,
            path_policy,
            classifier,
            ownership,
            protection,
            CleanupEligibilityPolicy(),
            ResidualRecentModificationPolicy(),
            self.file_operation_platform,
            self.recycle_bin_platform,
            max_selected=self.settings.residual_cleanup_max_selected,
            max_contained_objects=self.settings.residual_cleanup_max_contained_objects,
            max_total_bytes=self.settings.residual_cleanup_max_total_bytes,
        )
        preview = ResidualCleanupPreviewEngine(
            revalidator,
            CleanupRiskPolicy(
                max_normal_item_count=self.settings.residual_cleanup_normal_item_count,
                max_normal_object_count=self.settings.residual_cleanup_normal_object_count,
                max_normal_total_size=self.settings.residual_cleanup_normal_total_bytes,
                max_normal_single_item_size=(
                    self.settings.residual_cleanup_normal_single_item_bytes
                ),
            ),
            plan_ttl_seconds=self.settings.confirmation_ttl_seconds,
            preview_ttl_seconds=self.settings.confirmation_ttl_seconds,
        )
        registry = ToolRegistry(
            write_guard=ResidualCleanupExecutionGuard(self.residual_cleanup_repository)
        )
        registry.register(SoftwareResidualPrepareCleanupTool(revalidator))
        registry.register(
            SoftwareResidualTrashTool(
                self.residual_cleanup_repository,
                revalidator,
                self.file_operation_platform,
                self.recycle_bin_platform,
            )
        )
        confirmations = ResidualCleanupConfirmationService(
            self.residual_cleanup_repository,
            preview,
            plan_ttl_seconds=self.settings.confirmation_ttl_seconds,
            runtime_ttl_seconds=(self.settings.residual_cleanup_runtime_confirmation_ttl_seconds),
        )
        service = ResidualCleanupService(
            registry,
            preview,
            ResidualCleanupSafetyValidator(registry),
            confirmations,
            self.residual_cleanup_repository,
            self.file_operation_platform,
            ResidualCleanupAuditLogger(
                self.audit,
                app_version=__version__,
                git_commit=os.getenv("GITHUB_SHA"),
            ),
        )
        return ResidualCleanupServices(
            registry=registry,
            repository=self.residual_cleanup_repository,
            service=service,
        )

    def create_process_action_services(self) -> ProcessActionServices:
        """Build the Stage 4A resolver, policy, confirmations, registry, and executor."""
        self.require_feature(ReleaseFeature.PROCESS_ACTIONS)
        platform = self.process_management_platform
        own_process = platform.inspect_process(os.getpid())
        if own_process is None:
            raise RuntimeError("Cannot establish the Agent process identity safely")
        resolver = ProcessTargetResolver(platform, 2_000)
        compiler = ProcessActionPlanCompiler(resolver)
        policy = ProcessSafetyPolicy(
            current_owner_sid=own_process.identity.owner_sid,
            current_session_id=own_process.identity.session_id,
            agent_pids=frozenset({os.getpid()}),
        )
        guard = ProcessExecutionGuard(self.process_action_repository)
        registry = ToolRegistry(write_guard=guard)
        registry.register(RequestProcessExitTool(platform))
        registry.register(ForceTerminateProcessTool(platform))
        validator = ProcessActionSafetyValidator(
            registry,
            self.settings.process_action_max_applications,
            self.settings.process_action_max_processes,
        )
        audit = ProcessActionAuditLogger(
            self.audit,
            app_version=__version__,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        service = ProcessActionService(
            compiler,
            resolver,
            ProcessPreviewEngine(policy),
            validator,
            self.process_confirmation,
            self.process_action_repository,
            registry,
            audit,
            graceful_timeout_seconds=self.settings.process_graceful_timeout_seconds,
            force_timeout_seconds=self.settings.process_force_timeout_seconds,
        )
        return ProcessActionServices(
            registry=registry,
            platform=platform,
            resolver=resolver,
            compiler=compiler,
            service=service,
        )

    def create_startup_action_services(self) -> StartupActionServices:
        """Build the Stage 4B inventory, policy, encrypted backup, and narrow tools."""
        self.require_feature(ReleaseFeature.STARTUP_ACTIONS)
        platform = self.startup_management_platform
        resolver = StartupTargetResolver(platform, max_items=self.settings.diagnostic_max_items)
        policy = StartupSafetyPolicy(agent_root=Path(__file__).resolve().parents[1])
        guard = StartupExecutionGuard(self.startup_action_repository)
        registry = ToolRegistry(write_guard=guard)
        registry.register(
            DisableStartupTool(
                platform,
                self.startup_backup_vault,
                self.startup_action_repository,
            )
        )
        registry.register(
            RestoreStartupTool(
                platform,
                self.startup_backup_vault,
                self.startup_action_repository,
            )
        )
        audit = StartupActionAuditLogger(
            self.audit,
            app_version=__version__,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        service = StartupActionService(
            platform,
            resolver,
            policy,
            StartupPreviewEngine(policy),
            StartupActionSafetyValidator(registry),
            self.startup_confirmation,
            self.startup_backup_vault,
            self.startup_action_repository,
            registry,
            audit,
        )
        return StartupActionServices(
            registry=registry,
            platform=platform,
            resolver=resolver,
            service=service,
        )

    def create_service_action_services(self) -> ServiceActionServices:
        """Build Stage 4C1 exact identity, policy, two-step restart, and SCM tools."""
        self.require_feature(ReleaseFeature.SERVICE_ACTIONS)
        platform = self.service_control_platform
        resolver = ServiceTargetResolver(platform, max_items=self.settings.diagnostic_max_items)
        compiler = ServiceActionPlanCompiler()
        policy = ServiceSafetyPolicy(
            current_username=current_windows_username(),
            agent_root=Path(__file__).resolve().parents[1],
        )
        guard = ServiceExecutionGuard(self.service_action_repository)
        registry = ToolRegistry(write_guard=guard)

        def dispatched(request: object) -> None:
            from pc_manager_agent.domain.service_actions import (
                ServiceStepRequest,
                ServiceStepType,
                ServiceTransactionState,
            )

            typed = ServiceStepRequest.model_validate(request)
            self.service_action_repository.mark_dispatched(
                typed.transaction_id,
                (
                    ServiceTransactionState.WAITING_RUNNING
                    if typed.step is ServiceStepType.START
                    else ServiceTransactionState.WAITING_STOPPED
                ),
            )

        registry.register(StartServiceTool(platform, dispatched))
        registry.register(StopServiceTool(platform, dispatched))
        dependencies = ServiceDependencyAnalyzer()
        audit = ServiceActionAuditLogger(
            self.audit,
            app_version=__version__,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        service = ServiceActionService(
            platform,
            resolver,
            compiler,
            policy,
            ServicePreviewEngine(policy, dependencies),
            ServiceActionSafetyValidator(registry),
            self.service_confirmation,
            self.service_action_repository,
            registry,
            audit,
            timeout_seconds=self.settings.service_action_timeout_seconds,
        )
        return ServiceActionServices(
            registry=registry,
            platform=platform,
            resolver=resolver,
            compiler=compiler,
            service=service,
        )

    def create_privileged_action_services(
        self,
        fake_state: FakePrivilegedSystemState,
    ) -> PrivilegedActionServices:
        """Compose Mock-only protocol services when the explicit developer mode is enabled."""
        self.require_feature(ReleaseFeature.PRIVILEGED_BROKER)
        if self.settings.privileged_broker_mode != "mock":
            raise RuntimeError("Stage 4X1 privileged broker is disabled")
        if current_process_is_elevated():
            raise RuntimeError(
                "Stage 4X1 requires the main Agent to remain a standard-user process"
            )
        serializer = PrivilegedRequestSerializer(self.settings.privileged_max_request_bytes)
        authenticator = EphemeralHmacAuthenticator.generate()
        confirmations = PrivilegedActionConfirmationService(
            self.privileged_action_repository,
            plan_ttl_seconds=self.settings.confirmation_ttl_seconds,
            runtime_ttl_seconds=(self.settings.privileged_runtime_confirmation_ttl_seconds),
        )
        builder = PrivilegedActionBuilder(
            serializer,
            authenticator,
            confirmations,
            request_ttl_seconds=self.settings.privileged_request_ttl_seconds,
        )
        audit = PrivilegedActionAuditLogger(
            self.audit,
            app_version=__version__,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        caller = PrivilegedCallerContext(
            context_id=uuid4(),
            agent_instance_id=self.agent_instance_id,
            user_sid_fingerprint=_runtime_fingerprint(current_windows_username()),
            session_fingerprint=_runtime_fingerprint(f"{os.getpid()}:{self.agent_instance_id}"),
        )
        registry = build_stage4x1_registry(ServicePrivilegedRevalidator(fake_state))
        broker = MockPrivilegedBroker(
            serializer,
            authenticator,
            self.privileged_action_repository,
            PrivilegedRequestReplayStore(self.privileged_action_repository),
            registry,
            audit,
        )
        service = PrivilegedActionService(
            builder,
            confirmations,
            self.privileged_action_repository,
            serializer,
            broker,
            audit,
            caller,
        )
        return PrivilegedActionServices(service, broker, caller, fake_state)

    def create_windows_privileged_action_services(self) -> WindowsPrivilegedActionServices:
        """Compose the disabled-by-default real Broker route for exact service Start/Stop."""
        self.require_feature(ReleaseFeature.PRIVILEGED_BROKER)
        if self.settings.privileged_broker_mode != "windows":
            raise RuntimeError("Stage 4X2 Windows elevated Broker is disabled")
        if current_process_is_elevated():
            raise RuntimeError("The main Agent must remain a standard-user process")
        broker_path = self.settings.privileged_broker_path
        expected_sha = self.settings.privileged_broker_expected_sha256
        if broker_path is None or expected_sha is None:
            raise RuntimeError("The trusted Stage 4X2 Broker path and SHA-256 are required")
        serializer = PrivilegedRequestSerializer(self.settings.privileged_max_request_bytes)
        bootstrap_authenticator = EphemeralHmacAuthenticator.generate()
        confirmations = PrivilegedActionConfirmationService(
            self.privileged_action_repository,
            plan_ttl_seconds=self.settings.confirmation_ttl_seconds,
            runtime_ttl_seconds=self.settings.privileged_runtime_confirmation_ttl_seconds,
        )
        builder = PrivilegedActionBuilder(
            serializer,
            bootstrap_authenticator,
            confirmations,
            request_ttl_seconds=self.settings.privileged_request_ttl_seconds,
        )
        request_audit = PrivilegedActionAuditLogger(
            self.audit,
            app_version=__version__,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        os_identity = capture_current_process_identity()
        binary_inspector = WindowsBrokerBinaryInspector()
        availability = PrivilegedBrokerAvailabilityService(
            binary_inspector,
            enabled=True,
            broker_path=broker_path,
            expected_sha256=expected_sha,
            trust_mode=BrokerTrustMode(self.settings.privileged_broker_trust_mode.upper()),
            caller_identity=os_identity,
        )
        availability.require_ready()
        caller = PrivilegedCallerContext(
            context_id=uuid4(),
            agent_instance_id=self.agent_instance_id,
            user_sid_fingerprint=_runtime_fingerprint(os_identity.user_sid),
            session_fingerprint=_runtime_fingerprint(str(os_identity.session_id)),
        )
        service = PrivilegedActionService(
            builder,
            confirmations,
            self.privileged_action_repository,
            serializer,
            None,
            request_audit,
            caller,
            execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED,
        )
        elevated_audit = ElevatedBrokerAuditLogger(
            self.audit,
            app_version=__version__,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        verification_software = SoftwareTargetResolver(
            SoftwareInventoryService(WindowsSoftwareInventoryPlatform())
        )
        verification_msi = WindowsMsiProductInventory()
        coordinator = ElevatedServiceActionCoordinator(
            broker_path=broker_path,
            expected_broker_sha256=expected_sha,
            trust_mode=BrokerTrustMode(self.settings.privileged_broker_trust_mode.upper()),
            caller_identity=os_identity,
            launcher=WindowsUacBrokerLauncher(broker_path),
            binary_inspector=binary_inspector,
            pipe_client_factory=WindowsBrokerPipeClient,
            serializer=serializer,
            repository=self.privileged_action_repository,
            audit=elevated_audit,
            postcondition_verifier=Stage4X3PostconditionVerifier(
                self.service_control_platform,
                self.startup_management_platform,
                verification_software,
                verification_msi,
                max_items=self.settings.diagnostic_max_items,
            ),
            connect_timeout_seconds=(self.settings.privileged_broker_connect_timeout_seconds),
            message_timeout_seconds=(self.settings.privileged_broker_message_timeout_seconds),
        )
        return WindowsPrivilegedActionServices(service, coordinator, caller, availability)

    def create_elevated_service_preparation_service(
        self,
        source: ServiceActionServices,
    ) -> ElevatedServicePreparationService:
        """Bridge one Stage 4C1 permission-only Start/Stop block into Stage 4X2."""
        self.require_feature(ReleaseFeature.PRIVILEGED_BROKER)
        privileged = self.create_windows_privileged_action_services()
        return ElevatedServicePreparationService(
            privileged.service,
            privileged.coordinator,
            source.platform,
            ServiceSafetyPolicy(
                current_username=current_windows_username(),
                agent_root=Path(__file__).resolve().parents[1],
            ),
            ServiceDependencyAnalyzer(),
        )

    def create_stage4x3_action_services(self) -> Stage4X3ActionServices:
        """Compose the three explicit Stage 4X3 Main preparation and readback paths."""
        self.require_feature(ReleaseFeature.PRIVILEGED_BROKER)
        privileged = self.create_windows_privileged_action_services()
        software = SoftwareTargetResolver(
            SoftwareInventoryService(WindowsSoftwareInventoryPlatform())
        )
        msi_inventory = WindowsMsiProductInventory()
        service_base_policy = ServiceSafetyPolicy(
            current_username=current_windows_username(),
            agent_root=Path(__file__).resolve().parents[1],
        )
        preparation = ElevatedStage4X3PreparationService(
            privileged.service,
            privileged.coordinator,
            self.service_control_platform,
            self.service_startup_platform,
            ServiceStartupSafetyPolicy(service_base_policy),
            self.service_startup_backup_vault,
            self.service_startup_repository,
            self.startup_management_platform,
            MachineStartupSafetyPolicy(agent_root=Path(__file__).resolve().parents[1]),
            self.startup_backup_vault,
            self.startup_action_repository,
            software,
            UninstallCapabilityResolver(),
            MsiProductValidator(msi_inventory),
            SoftwareUninstallSafetyPolicy(agent_root=Path(__file__).resolve().parents[1]),
            MachineMsiExecutionPolicy(),
            SoftwareExecutionPreflight(
                WindowsSystemDiagnosticsPlatform(),
                max_items=self.settings.diagnostic_max_items,
            ),
            self.msi_uninstall_repository,
            self.privileged_action_repository,
            max_items=self.settings.diagnostic_max_items,
        )
        return Stage4X3ActionServices(preparation)

    def create_service_startup_action_services(self) -> ServiceStartupActionServices:
        """Build Stage 4C2 backup, Preview, confirmation, and narrow SCM tools."""
        self.require_feature(ReleaseFeature.SERVICE_ACTIONS)
        resolver = ServiceTargetResolver(
            self.service_control_platform,
            max_items=self.settings.diagnostic_max_items,
        )
        base_policy = ServiceSafetyPolicy(
            current_username=current_windows_username(),
            agent_root=Path(__file__).resolve().parents[1],
        )
        policy = ServiceStartupSafetyPolicy(base_policy)
        registry = ToolRegistry(
            write_guard=ServiceStartupExecutionGuard(self.service_startup_repository)
        )
        registry.register(
            SetServiceAutomaticTool(
                self.service_startup_platform,
                self.service_startup_backup_vault,
            )
        )
        registry.register(
            SetServiceManualTool(
                self.service_startup_platform,
                self.service_startup_backup_vault,
            )
        )
        registry.register(
            RestoreServiceStartupTool(
                self.service_startup_platform,
                self.service_startup_backup_vault,
            )
        )
        audit = ServiceStartupActionAuditLogger(
            self.audit,
            app_version=__version__,
            git_commit=os.getenv("GITHUB_SHA"),
        )
        service = ServiceStartupActionService(
            self.service_control_platform,
            self.service_startup_platform,
            resolver,
            policy,
            ServiceStartupPreviewEngine(policy),
            ServiceStartupSafetyValidator(),
            self.service_startup_confirmation,
            self.service_startup_backup_vault,
            self.service_startup_repository,
            registry,
            audit,
        )
        return ServiceStartupActionServices(
            registry=registry,
            platform=self.service_startup_platform,
            resolver=resolver,
            service=service,
        )

    def close(self) -> None:
        """Release local persistence resources."""
        self.tasks.close()
        self.agents.close()
        self.browser.close()
        self.office.close()
        self.optimization_report_store.clear()
        if self._optimization_reviews is not None:
            self._optimization_reviews.handoffs.clear()
        self.optimization_session_repository.close()
        self.optimization_result_reader.close()
        self.system_cleanup_repository.close()
        self.privileged_action_repository.close()
        self.residual_cleanup_repository.close()
        self.software_residual_repository.close()
        self.msix_uninstall_repository.close()
        self.winget_uninstall_repository.close()
        self.vendor_uninstall_repository.close()
        self.msi_uninstall_repository.close()
        self.service_startup_backup_vault.close()
        self.service_startup_repository.close()
        self.service_action_repository.close()
        self.startup_backup_vault.close()
        self.startup_action_repository.close()
        self.process_action_repository.close()
        self.operation_repository.close()
        self.analysis_results.close()
        self.authorized_path_repository.close()
        self.audit.close()

    def _audit_external_consent(self, request: ExternalDataConsentRequest) -> None:
        self.audit.record(
            AuditEvent(
                event_type="external_data.confirmation.resolved",
                parameters={
                    "purpose": request.purpose.value,
                    "provider": request.provider,
                    "payload_digest": request.payload_digest,
                },
                risk_level=RiskLevel.R2,
                confirmation_required=True,
                confirmation_result=request.state.value,
                app_version=__version__,
            )
        )

    def _audit_authorized_path_change(self, action: str, record: AuthorizedPath) -> None:
        self.audit.record(
            AuditEvent(
                event_type=f"authorized_path.{action}",
                parameters={
                    "path_id": str(record.path_id),
                    "path": str(record.path),
                    "kind": record.kind.value,
                    "favorite": record.favorite,
                },
                risk_level=RiskLevel.R1,
                confirmation_required=True,
                confirmation_result="APPROVED",
                rollback={
                    "level": "FULL",
                    "inverse_action": "remove" if action == "added" else "restore",
                    "record": record.model_dump(mode="json"),
                },
                app_version=__version__,
            )
        )

    def _audit_report_export(self, result: ReportExportResult) -> None:
        self.audit.record(
            AuditEvent(
                event_type="report.exported",
                parameters={"path": str(result.path), "format": result.format.value},
                risk_level=RiskLevel.R1,
                confirmation_required=True,
                confirmation_result="APPROVED",
                result={"rows": result.rows, "size_bytes": result.size_bytes},
                rollback={
                    "level": "MANUAL",
                    "reason": "Stage 1 never automatically deletes an exported user report",
                },
                app_version=__version__,
            )
        )


def _runtime_fingerprint(value: str) -> str:
    """Hash Mock caller labels; these fingerprints are not authentication secrets."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
