"""Dependency composition for GUI and headless tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pc_manager_agent import __version__
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.authorization.models import AuthorizedPath
from pc_manager_agent.authorization.service import AuthorizedPathService
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.confirmation.external_data import (
    ExternalDataConsentRequest,
    ExternalDataConsentService,
)
from pc_manager_agent.confirmation.state_machine import ConfirmationService
from pc_manager_agent.domain.file_analysis import FileAnalysisProgress
from pc_manager_agent.domain.reports import ScanProgress
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.orchestration.explanation import FileAnalysisExplainer
from pc_manager_agent.orchestration.file_analysis import FileAnalysisOrchestrator
from pc_manager_agent.orchestration.file_analysis_planner import (
    FileAnalysisPlanCompiler,
    FileAnalysisPlanner,
)
from pc_manager_agent.orchestration.service import ScanOrchestrator
from pc_manager_agent.persistence.analysis_results import AnalysisResultRepository
from pc_manager_agent.persistence.authorized_paths import AuthorizedPathRepository
from pc_manager_agent.platform_support.windows.explorer import WindowsExplorerService
from pc_manager_agent.platform_support.windows.path_info import (
    is_network_path,
    last_access_time_reliable,
)
from pc_manager_agent.providers.llm.base import LLMProvider
from pc_manager_agent.providers.llm.openai_provider import OpenAILLMProvider
from pc_manager_agent.reporting.exporter import ReportExporter, ReportExportResult
from pc_manager_agent.safety.file_analysis_validator import FileAnalysisSafetyValidator
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.safety.plan_reviewer import SafetyReviewer
from pc_manager_agent.tools.file_tools.duplicate_analyzer import DuplicateFileAnalyzer
from pc_manager_agent.tools.file_tools.hashing import SafeFileHasher
from pc_manager_agent.tools.file_tools.inactive_file_analyzer import InactiveFileAnalyzer
from pc_manager_agent.tools.file_tools.large_file_analyzer import LargeFileAnalyzer
from pc_manager_agent.tools.file_tools.scanner import DirectoryScannerTool
from pc_manager_agent.tools.registry import ToolRegistry


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


class ApplicationRuntime:
    """Own shared infrastructure and create root-scoped orchestrators."""

    def __init__(self, settings: AppSettings) -> None:
        """
        1.保存应用配置,settings 包含审计数据库路径、扫描数量上限、超时时间、确认有效期以及模型配置等
        2.初始化审计数据库
        3.创建确认服务
        """
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
        self.report_exporter = ReportExporter(
            self.analysis_results,
            on_export=self._audit_report_export,
        )
        self.explorer = WindowsExplorerService(self.authorized_paths)

    def create_scan_orchestrator(self, root: Path) -> ScanOrchestrator:
        """Create a new registry and reviewer limited to exactly one user-selected root."""
        """当用户选择目录并生成计划时,会被调用"""
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

    def close(self) -> None:
        """Release local persistence resources."""
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
