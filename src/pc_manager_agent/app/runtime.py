"""Dependency composition for GUI and headless tests."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pc_manager_agent import __version__
from pc_manager_agent.audit.file_operations import OperationAuditLogger
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.process_actions import ProcessActionAuditLogger
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.service_actions import ServiceActionAuditLogger
from pc_manager_agent.audit.startup_actions import StartupActionAuditLogger
from pc_manager_agent.audit.system_diagnostics import DiagnosticAuditLogger
from pc_manager_agent.audit.trash import TrashAuditLogger
from pc_manager_agent.authorization.models import AuthorizedPath
from pc_manager_agent.authorization.service import AuthorizedPathService
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.confirmation.external_data import (
    ExternalDataConsentRequest,
    ExternalDataConsentService,
)
from pc_manager_agent.confirmation.file_operations import (
    OperationConfirmationService,
    RollbackConfirmationService,
)
from pc_manager_agent.confirmation.process_actions import ProcessActionConfirmationService
from pc_manager_agent.confirmation.service_actions import ServiceActionConfirmationService
from pc_manager_agent.confirmation.startup_actions import StartupActionConfirmationService
from pc_manager_agent.confirmation.state_machine import ConfirmationService
from pc_manager_agent.confirmation.system_diagnostics import DiagnosticConfirmationService
from pc_manager_agent.confirmation.trash import TrashConfirmationService
from pc_manager_agent.domain.file_analysis import FileAnalysisProgress
from pc_manager_agent.domain.reports import ScanProgress
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.transactions import OperationProgress
from pc_manager_agent.domain.trash import TrashExecutionReport
from pc_manager_agent.orchestration.diagnostic_engine import DiagnosticEngine
from pc_manager_agent.orchestration.diagnostic_provider import (
    DiagnosticExplainer,
    DiagnosticProviderPlanner,
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
from pc_manager_agent.orchestration.process_action_planner import ProcessActionPlanCompiler
from pc_manager_agent.orchestration.process_actions import ProcessActionService
from pc_manager_agent.orchestration.process_target_resolver import ProcessTargetResolver
from pc_manager_agent.orchestration.service import ScanOrchestrator
from pc_manager_agent.orchestration.service_action_planner import ServiceActionPlanCompiler
from pc_manager_agent.orchestration.service_actions import ServiceActionService
from pc_manager_agent.orchestration.service_dependency_analyzer import ServiceDependencyAnalyzer
from pc_manager_agent.orchestration.service_target_resolver import ServiceTargetResolver
from pc_manager_agent.orchestration.startup_actions import StartupActionService
from pc_manager_agent.orchestration.startup_target_resolver import StartupTargetResolver
from pc_manager_agent.orchestration.system_diagnostic_planner import DiagnosticPlanCompiler
from pc_manager_agent.orchestration.system_diagnostics import (
    DiagnosticOrchestrator,
    SystemSnapshotService,
)
from pc_manager_agent.orchestration.transaction_executor import TransactionExecutor
from pc_manager_agent.orchestration.trash_planner import TrashPlanCompiler
from pc_manager_agent.orchestration.trash_service import TrashService
from pc_manager_agent.persistence.analysis_results import AnalysisResultRepository
from pc_manager_agent.persistence.authorized_paths import AuthorizedPathRepository
from pc_manager_agent.persistence.file_operations import (
    OperationRepository,
    TransactionExecutionGuard,
)
from pc_manager_agent.persistence.process_actions import (
    ProcessActionRepository,
    ProcessExecutionGuard,
)
from pc_manager_agent.persistence.service_actions import (
    ServiceActionRepository,
    ServiceExecutionGuard,
)
from pc_manager_agent.persistence.startup_actions import (
    StartupActionRepository,
    StartupBackupVault,
    StartupExecutionGuard,
)
from pc_manager_agent.platform_support.processes import ProcessManagementPlatform
from pc_manager_agent.platform_support.service_control import ServiceControlPlatform
from pc_manager_agent.platform_support.startup import StartupManagementPlatform
from pc_manager_agent.platform_support.windows.data_protection import (
    WindowsCurrentUserDataProtector,
)
from pc_manager_agent.platform_support.windows.explorer import WindowsExplorerService
from pc_manager_agent.platform_support.windows.file_operations import (
    WindowsFileOperationPlatform,
)
from pc_manager_agent.platform_support.windows.path_info import (
    is_network_path,
    last_access_time_reliable,
)
from pc_manager_agent.platform_support.windows.process_management import (
    WindowsProcessManagementPlatform,
)
from pc_manager_agent.platform_support.windows.recycle_bin import WindowsRecycleBinPlatform
from pc_manager_agent.platform_support.windows.service_control import (
    WindowsServiceControlPlatform,
    current_windows_username,
)
from pc_manager_agent.platform_support.windows.startup_management import (
    WindowsStartupManagementPlatform,
)
from pc_manager_agent.platform_support.windows.system_diagnostics import (
    WindowsSystemDiagnosticsPlatform,
)
from pc_manager_agent.providers.llm.base import LLMProvider
from pc_manager_agent.providers.llm.openai_provider import OpenAILLMProvider
from pc_manager_agent.reporting.exporter import ReportExporter, ReportExportResult
from pc_manager_agent.rollback.manager import RollbackManager
from pc_manager_agent.safety.file_analysis_validator import FileAnalysisSafetyValidator
from pc_manager_agent.safety.file_operation_validator import FileOperationSafetyValidator
from pc_manager_agent.safety.operation_preview import OperationPreviewEngine
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.safety.plan_reviewer import SafetyReviewer
from pc_manager_agent.safety.process_policy import ProcessSafetyPolicy
from pc_manager_agent.safety.process_preview import ProcessPreviewEngine
from pc_manager_agent.safety.process_validator import ProcessActionSafetyValidator
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.safety.service_preview import ServicePreviewEngine
from pc_manager_agent.safety.service_validator import ServiceActionSafetyValidator
from pc_manager_agent.safety.startup_policy import StartupSafetyPolicy
from pc_manager_agent.safety.startup_preview import StartupPreviewEngine
from pc_manager_agent.safety.startup_validator import StartupActionSafetyValidator
from pc_manager_agent.safety.system_diagnostics import DiagnosticSafetyValidator
from pc_manager_agent.safety.trash_policy import TrashPathPolicy
from pc_manager_agent.safety.trash_preview import TrashPreviewEngine
from pc_manager_agent.safety.trash_validator import TrashSafetyValidator
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
from pc_manager_agent.tools.system_tools.process_actions import (
    ForceTerminateProcessTool,
    RequestProcessExitTool,
)
from pc_manager_agent.tools.system_tools.service_actions import (
    StartServiceTool,
    StopServiceTool,
)
from pc_manager_agent.tools.system_tools.startup_actions import (
    DisableStartupTool,
    RestoreStartupTool,
)


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


class ApplicationRuntime:
    """Own shared infrastructure and create root-scoped orchestrators."""

    def __init__(self, settings: AppSettings) -> None:
        """保存配置并初始化审计、确认、授权目录和分析结果等共享服务。"""
        self.settings = settings
        self.audit = AuditRepository(settings.database_path)
        self.audit.initialize()
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
        self.file_operation_platform = WindowsFileOperationPlatform()
        self.recycle_bin_platform = WindowsRecycleBinPlatform()
        self.process_management_platform = WindowsProcessManagementPlatform()
        self.startup_management_platform = WindowsStartupManagementPlatform(
            settings.data_directory / "disabled_startup"
        )
        self.service_control_platform = WindowsServiceControlPlatform()
        self.report_exporter = ReportExporter(
            self.analysis_results,
            on_export=self._audit_report_export,
        )
        self.explorer = WindowsExplorerService(self.authorized_paths)

    def create_scan_orchestrator(self, root: Path) -> ScanOrchestrator:
        """为一个用户选择的根目录创建独立路径策略、注册表和扫描编排器。"""
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

    def create_process_action_services(self) -> ProcessActionServices:
        """Build the Stage 4A resolver, policy, confirmations, registry, and executor."""
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

    def close(self) -> None:
        """Release local persistence resources."""
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
