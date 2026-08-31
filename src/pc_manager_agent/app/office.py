"""Composition root for independent Office services; no Windows-management registry changes."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pc_manager_agent.audit.office_documents import OfficeAudit
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.authorization.office_documents import OfficePathGrants
from pc_manager_agent.backup.office_documents import DocumentBackupService
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.confirmation.office_documents import DocumentConfirmations
from pc_manager_agent.office.worker import BoundedOfficeParser
from pc_manager_agent.orchestration.office_documents import OfficeDocumentService
from pc_manager_agent.orchestration.office_edits import OfficeEditService
from pc_manager_agent.orchestration.office_model import OfficeModelService
from pc_manager_agent.persistence.office_documents import OfficeRepository
from pc_manager_agent.platform_support.windows.office_files import (
    WindowsOfficeFiles,
    office_main_is_elevated,
)
from pc_manager_agent.platform_support.windows.office_protection import WindowsOfficeDataProtector
from pc_manager_agent.providers.llm.openai_office import OpenAIOfficeProvider


@dataclass(frozen=True)
class OfficeServices:
    """One session's grants, document workflows and optional external proposal service."""

    reads: OfficeDocumentService
    edits: OfficeEditService
    model: OfficeModelService | None
    repository: OfficeRepository

    def close(self) -> None:
        """Close after workers stop; parsed document bodies are dropped, backups remain."""
        self.reads.clear_results()
        self.repository.close()


def build_office_services(
    settings: AppSettings,
    audit_repository: AuditRepository,
    forbidden_roots: Callable[[], tuple[Path, ...]],
) -> OfficeServices:
    """Inject fixed Windows adapters; Office never inherits a system tool or elevated Broker."""
    limits = settings.office_limits
    repository = OfficeRepository(settings.database_path)
    audit = OfficeAudit(audit_repository, git_commit=os.getenv("GITHUB_SHA"))
    files, codec = WindowsOfficeFiles(), BoundedOfficeParser(limits)
    confirmations = DocumentConfirmations(repository, audit)
    reads = OfficeDocumentService(
        OfficePathGrants(forbidden_roots), files, codec, confirmations, audit, limits
    )
    backups = DocumentBackupService(
        settings.data_directory / "office-backups",
        repository,
        files,
        WindowsOfficeDataProtector(),
        limits,
    )
    edits = OfficeEditService(
        reads,
        files,
        codec,
        repository,
        backups,
        confirmations,
        audit,
        limits,
        is_elevated=office_main_is_elevated,
    )
    model = None
    if settings.llm_provider == "openai" and settings.openai_model and settings.openai_api_key:
        model = OfficeModelService(
            reads,
            OpenAIOfficeProvider(settings.openai_model, settings.openai_api_key.get_secret_value()),
            confirmations,
            audit,
        )
    return OfficeServices(reads, edits, model, repository)
