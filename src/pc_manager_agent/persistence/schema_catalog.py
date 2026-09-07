"""Complete SQLite schema catalog used only by versioned production migrations."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Connection, MetaData

from pc_manager_agent.audit.repository import Base as AuditBase
from pc_manager_agent.persistence.analysis_results import AnalysisBase
from pc_manager_agent.persistence.authorized_paths import AuthorizationBase
from pc_manager_agent.persistence.browser import browser_metadata
from pc_manager_agent.persistence.computer_tasks import ComputerTaskBase
from pc_manager_agent.persistence.file_operations import OperationBase
from pc_manager_agent.persistence.memory import MemoryBase
from pc_manager_agent.persistence.msix_uninstall import MsixUninstallBase
from pc_manager_agent.persistence.office_documents import office_metadata
from pc_manager_agent.persistence.optimization_sessions import OptimizationSessionBase
from pc_manager_agent.persistence.privileged_actions import PrivilegedActionBase
from pc_manager_agent.persistence.process_actions import ProcessActionBase
from pc_manager_agent.persistence.residual_cleanup import ResidualCleanupBase
from pc_manager_agent.persistence.service_actions import ServiceActionBase
from pc_manager_agent.persistence.service_startup_actions import ServiceStartupBase
from pc_manager_agent.persistence.software_residuals import SoftwareResidualBase
from pc_manager_agent.persistence.software_uninstall_execution import MsiUninstallBase
from pc_manager_agent.persistence.startup_actions import StartupBase
from pc_manager_agent.persistence.system_cleanup import SystemCleanupBase
from pc_manager_agent.persistence.task_runtime import TaskJournalBase
from pc_manager_agent.persistence.vendor_uninstall import VendorUninstallBase
from pc_manager_agent.persistence.voice import voice_metadata
from pc_manager_agent.persistence.winget_uninstall import WingetUninstallBase


def schema_metadata() -> tuple[MetaData, ...]:
    """Return every durable schema; adding persistence requires updating this catalog."""
    return (
        AuditBase.metadata,
        AnalysisBase.metadata,
        AuthorizationBase.metadata,
        browser_metadata,
        ComputerTaskBase.metadata,
        OperationBase.metadata,
        MemoryBase.metadata,
        MsixUninstallBase.metadata,
        office_metadata,
        OptimizationSessionBase.metadata,
        PrivilegedActionBase.metadata,
        ProcessActionBase.metadata,
        ResidualCleanupBase.metadata,
        ServiceActionBase.metadata,
        ServiceStartupBase.metadata,
        SoftwareResidualBase.metadata,
        MsiUninstallBase.metadata,
        StartupBase.metadata,
        SystemCleanupBase.metadata,
        TaskJournalBase.metadata,
        VendorUninstallBase.metadata,
        voice_metadata,
        WingetUninstallBase.metadata,
    )


def create_current_schema(connection: Connection) -> None:
    """Create the current additive schema inside the migration transaction."""
    for metadata in schema_metadata():
        metadata.create_all(connection)


def expected_table_names(metadata_items: Iterable[MetaData] | None = None) -> frozenset[str]:
    """Return the exact durable table names expected at the current schema version."""
    selected = tuple(metadata_items) if metadata_items is not None else schema_metadata()
    return frozenset(table.name for metadata in selected for table in metadata.sorted_tables)
