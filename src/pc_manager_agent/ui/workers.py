"""Qt worker objects for non-blocking deterministic tool execution."""

from __future__ import annotations

import asyncio
from uuid import UUID

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime, FileOperationServices, TrashServices
from pc_manager_agent.domain.file_analysis import FileAnalysisPlan, FileAnalysisReport
from pc_manager_agent.domain.file_operations import FileOperationPlan
from pc_manager_agent.domain.plans import TaskPlan
from pc_manager_agent.domain.reports import ScanReport
from pc_manager_agent.domain.transactions import OperationExecutionReport, OperationTransaction
from pc_manager_agent.domain.trash import TrashExecutionReport, TrashPlan
from pc_manager_agent.orchestration.explanation import FileAnalysisExplainer
from pc_manager_agent.orchestration.file_analysis_planner import (
    FileAnalysisPlanner,
    FileAnalysisPlanningResult,
)
from pc_manager_agent.orchestration.file_operation_service import PreparedFileOperation
from pc_manager_agent.orchestration.service import ScanOrchestrator
from pc_manager_agent.orchestration.trash_service import (
    PreparedTrashOperation,
    RuntimeConfirmedTrashOperation,
)
from pc_manager_agent.rollback.manager import PreparedRollback, RollbackManager
from pc_manager_agent.tools.manifest import CancellationToken


class ScanWorkerSignals(QObject):
    """Thread-safe signals emitted by a scan worker."""

    completed = Signal(object)
    failed = Signal(str)


class ScanWorker(QRunnable):
    """Execute one already-confirmed scan outside the GUI thread."""

    def __init__(self, orchestrator: ScanOrchestrator, plan: TaskPlan) -> None:
        super().__init__()
        self.signals = ScanWorkerSignals()
        self.cancellation = CancellationToken()
        self._orchestrator = orchestrator
        self._plan = plan

    @Slot()
    def run(self) -> None:
        """Execute and convert exceptions to a user-safe signal."""
        try:
            report = self._orchestrator.execute(self._plan, self.cancellation)
        except Exception as exc:  # Qt worker boundary must report all failures.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(report)

    def cancel(self) -> None:
        """Request cooperative cancellation."""
        self.cancellation.cancel()


def require_scan_report(value: object) -> ScanReport:
    """Narrow an object transported through a Qt signal."""
    if not isinstance(value, ScanReport):
        msg = "Worker emitted an invalid scan report"
        raise TypeError(msg)
    return value


class FileAnalysisWorkerSignals(QObject):
    """Thread-safe progress and terminal signals for a Stage 1 execution."""

    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(str)


class FileAnalysisWorker(QRunnable):
    """Build execution services and run one confirmed plan off the GUI thread."""

    def __init__(self, runtime: ApplicationRuntime, plan: FileAnalysisPlan) -> None:
        super().__init__()
        self.signals = FileAnalysisWorkerSignals()
        self.cancellation = CancellationToken()
        self._plan = plan
        self._services = runtime.create_file_analysis_services(self.signals.progress.emit)

    @Slot()
    def run(self) -> None:
        """Execute the confirmed plan and convert failures to a safe signal."""
        try:
            report = self._services.orchestrator.execute(
                self._plan,
                self.cancellation,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(report)

    def cancel(self) -> None:
        """Request cooperative cancellation."""
        self.cancellation.cancel()


class PlannerWorkerSignals(QObject):
    """Terminal signals for an asynchronous provider planning call."""

    completed = Signal(object)
    failed = Signal(str)


class PlannerWorker(QRunnable):
    """Run an already-consented provider planning call outside the GUI thread."""

    def __init__(
        self,
        planner: FileAnalysisPlanner,
        user_goal: str,
        confirmation_id: UUID,
        root_ids: tuple[UUID, ...],
    ) -> None:
        super().__init__()
        self.signals = PlannerWorkerSignals()
        self._planner = planner
        self._user_goal = user_goal
        self._confirmation_id = confirmation_id
        self._root_ids = root_ids

    @Slot()
    def run(self) -> None:
        """Create a provider intent and compile it into a deterministic plan."""
        try:
            result = asyncio.run(
                self._planner.plan(
                    self._user_goal,
                    self._confirmation_id,
                    self._root_ids,
                )
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(result)


class ExplanationWorkerSignals(QObject):
    """Terminal signals for an aggregate-only provider explanation."""

    completed = Signal(str)
    failed = Signal(str)


class ExplanationWorker(QRunnable):
    """Request one already-consented aggregate explanation off the GUI thread."""

    def __init__(
        self,
        explainer: FileAnalysisExplainer,
        plan: FileAnalysisPlan,
        report: FileAnalysisReport,
        confirmation_id: UUID,
    ) -> None:
        super().__init__()
        self.signals = ExplanationWorkerSignals()
        self._explainer = explainer
        self._plan = plan
        self._report = report
        self._confirmation_id = confirmation_id

    @Slot()
    def run(self) -> None:
        """Return provider observations with deterministic numeric rendering."""
        try:
            text = asyncio.run(
                self._explainer.explain(
                    self._plan,
                    self._report.summary,
                    self._confirmation_id,
                )
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(text)


def require_analysis_report(value: object) -> FileAnalysisReport:
    """Narrow a Stage 1 report transported through a Qt signal."""
    if not isinstance(value, FileAnalysisReport):
        raise TypeError("Worker emitted an invalid file analysis report")
    return value


def require_planning_result(value: object) -> FileAnalysisPlanningResult:
    """Narrow a provider planning result transported through a Qt signal."""
    if not isinstance(value, FileAnalysisPlanningResult):
        raise TypeError("Worker emitted an invalid file analysis planning result")
    return value


class OperationWorkerSignals(QObject):
    """Thread-safe Stage 2A planning, Preview, execution, and rollback signals."""

    completed = Signal(object)
    failed = Signal(str)


class FileOperationPlanningWorker(QRunnable):
    """Resolve one provider intent into a concrete local file-operation plan."""

    def __init__(
        self,
        services: FileOperationServices,
        user_goal: str,
        confirmation_id: UUID,
    ) -> None:
        super().__init__()
        if services.planner is None:
            raise ValueError("A model provider is not configured")
        self.signals = OperationWorkerSignals()
        self._services = services
        self._user_goal = user_goal
        self._confirmation_id = confirmation_id

    @Slot()
    def run(self) -> None:
        """Request typed intent, discover authorized sources, and compile exact paths."""
        planner = self._services.planner
        if planner is None:
            self.signals.failed.emit("ValueError: A model provider is not configured")
            return
        try:
            intent_result = asyncio.run(
                planner.plan_intent(
                    self._user_goal,
                    self._confirmation_id,
                )
            )
            sources = self._services.source_resolver.resolve(intent_result.intent.selection)
            plan = self._services.compiler.compile(
                self._user_goal,
                intent_result.intent,
                sources,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(plan)


class OperationPreviewWorker(QRunnable):
    """Perform live Preview and persistence away from the GUI thread."""

    def __init__(self, services: FileOperationServices, plan: FileOperationPlan) -> None:
        super().__init__()
        self.signals = OperationWorkerSignals()
        self._services = services
        self._plan = plan

    @Slot()
    def run(self) -> None:
        """Review and Preview without executing a filesystem mutation."""
        try:
            prepared = self._services.service.prepare(self._plan)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(prepared)


class OperationExecutionWorker(QRunnable):
    """Execute one confirmed transaction and stop before every cancelled future item."""

    def __init__(
        self,
        services: FileOperationServices,
        prepared: PreparedFileOperation,
    ) -> None:
        super().__init__()
        self.signals = OperationWorkerSignals()
        self.cancellation = CancellationToken()
        self._services = services
        self._prepared = prepared

    @Slot()
    def run(self) -> None:
        """Execute registered tools and emit only a verified persistent report."""
        try:
            report = self._services.service.execute(self._prepared, self.cancellation)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(report)

    def cancel(self) -> None:
        """Stop future operations; an already-started Win32 call is not interrupted."""
        self.cancellation.cancel()


class RollbackExecutionWorker(QRunnable):
    """Execute a separately confirmed rollback away from the GUI thread."""

    def __init__(self, manager: RollbackManager, prepared: PreparedRollback) -> None:
        super().__init__()
        self.signals = OperationWorkerSignals()
        self.cancellation = CancellationToken()
        self._manager = manager
        self._prepared = prepared

    @Slot()
    def run(self) -> None:
        """Execute reverse operations in persisted descending sequence."""
        try:
            transaction = self._manager.execute(self._prepared, self.cancellation)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(transaction)

    def cancel(self) -> None:
        """Stop future rollback items without interrupting an active Win32 call."""
        self.cancellation.cancel()


def require_operation_plan(value: object) -> FileOperationPlan:
    """Narrow an operation plan transported through a Qt signal."""
    if not isinstance(value, FileOperationPlan):
        raise TypeError("Worker emitted an invalid file-operation plan")
    return value


def require_prepared_operation(value: object) -> PreparedFileOperation:
    """Narrow a prepared operation transported through a Qt signal."""
    if not isinstance(value, PreparedFileOperation):
        raise TypeError("Worker emitted an invalid operation Preview")
    return value


def require_operation_report(value: object) -> OperationExecutionReport:
    """Narrow an operation execution report transported through a Qt signal."""
    if not isinstance(value, OperationExecutionReport):
        raise TypeError("Worker emitted an invalid file-operation report")
    return value


def require_operation_transaction(value: object) -> OperationTransaction:
    """Narrow a rollback transaction transported through a Qt signal."""
    if not isinstance(value, OperationTransaction):
        raise TypeError("Worker emitted an invalid rollback result")
    return value


class TrashPreviewWorker(QRunnable):
    """Perform Stage 2B safety review, snapshot, persistence, and first request off UI."""

    def __init__(self, services: TrashServices, plan: TrashPlan) -> None:
        super().__init__()
        self.signals = OperationWorkerSignals()
        self._services = services
        self._plan = plan

    @Slot()
    def run(self) -> None:
        """Prepare an R2 transaction without performing a filesystem mutation."""
        try:
            prepared = self._services.service.prepare(self._plan)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(prepared)


class TrashExecutionWorker(QRunnable):
    """Execute one twice-confirmed Stage 2B transaction away from the UI thread."""

    def __init__(
        self,
        services: TrashServices,
        prepared: RuntimeConfirmedTrashOperation,
    ) -> None:
        super().__init__()
        self.signals = OperationWorkerSignals()
        self.cancellation = CancellationToken()
        self._services = services
        self._prepared = prepared

    @Slot()
    def run(self) -> None:
        """Run the registered Recycle Bin tool and emit a verified terminal report."""
        try:
            report = self._services.service.execute(self._prepared, self.cancellation)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(report)

    def cancel(self) -> None:
        """Stop future objects without interrupting the current Shell operation."""
        self.cancellation.cancel()


def require_prepared_trash(value: object) -> PreparedTrashOperation:
    """Narrow a Stage 2B prepared transaction transported through a Qt signal."""
    if not isinstance(value, PreparedTrashOperation):
        raise TypeError("Worker emitted an invalid trash Preview")
    return value


def require_trash_report(value: object) -> TrashExecutionReport:
    """Narrow a Stage 2B execution report transported through a Qt signal."""
    if not isinstance(value, TrashExecutionReport):
        raise TypeError("Worker emitted an invalid trash execution report")
    return value
