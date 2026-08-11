"""Provider intent boundary and deterministic Stage 2A operation compilation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pc_manager_agent.authorization.service import AuthorizedPathService
from pc_manager_agent.confirmation.external_data import (
    ExternalDataConsentRequest,
    ExternalDataConsentService,
    ExternalDataPurpose,
)
from pc_manager_agent.domain.file_operations import (
    FileObjectKind,
    FileOperationIntentDraft,
    FileOperationPlan,
    FileSelectionRule,
    FileState,
    OperationType,
    OrganizationGroup,
    PlannedFileOperation,
    RenameRule,
    RenameRuleType,
)
from pc_manager_agent.platform_support.base import FileOperationPlatform
from pc_manager_agent.providers.llm.base import (
    AuthorizedRootOption,
    FileOperationPlannerRequest,
    LLMProvider,
)
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.tools.registry import ToolRegistry, UnknownToolError


@dataclass(frozen=True, slots=True)
class FileOperationIntentResult:
    """Provider-produced intent and trace metadata awaiting local file discovery."""

    intent: FileOperationIntentDraft
    provider: str
    provider_request_id: str | None


class FileOperationSourceResolver:
    """Discover bounded file sources from authorized IDs and literal extensions."""

    def __init__(
        self,
        authorization: AuthorizedPathService,
        *,
        max_sources: int = 500,
    ) -> None:
        if max_sources <= 0:
            raise ValueError("Source limit must be positive")
        self._authorization = authorization
        self._max_sources = max_sources

    def resolve(self, selection: FileSelectionRule) -> tuple[Path, ...]:
        """Resolve a provider selector locally without following redirected directories."""
        if selection.record_ids and not selection.extensions:
            raise ValueError("Record-only provider selection is unavailable in Stage 2A")
        policy = self._authorization.build_policy(selection.root_ids)
        roots = self._authorization.resolve_authorized(selection.root_ids)
        discovered: dict[str, Path] = {}
        for record in roots:
            stack = [record.path]
            while stack:
                directory = stack.pop()
                with os.scandir(directory) as entries:
                    for entry in entries:
                        path = Path(entry.path)
                        if policy.entry_rejection_reason(path) is not None:
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if selection.recursive:
                                stack.append(path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        if path.suffix.casefold() not in selection.extensions:
                            continue
                        canonical = policy.validate_operation_source(path)
                        discovered[os.path.normcase(os.fspath(canonical))] = canonical
                        if len(discovered) > self._max_sources:
                            raise ValueError(
                                f"Source selection exceeds the limit of {self._max_sources}"
                            )
        return tuple(sorted(discovered.values(), key=lambda path: str(path).casefold()))


class FileOperationPlanCompiler:
    """Resolve authorized IDs and compute every concrete path from live metadata."""

    def __init__(
        self,
        authorization: AuthorizedPathService,
        platform: FileOperationPlatform,
        *,
        max_operations: int = 500,
    ) -> None:
        self._authorization = authorization
        self._platform = platform
        self._max_operations = max_operations

    def compile(
        self,
        user_goal: str,
        intent: FileOperationIntentDraft,
        sources: tuple[Path, ...],
    ) -> FileOperationPlan:
        """Compile a finite intent and explicit discovered objects into a concrete R1 plan."""
        if not sources:
            raise ValueError("No discovered source object matches the operation intent")
        root_ids = list(intent.selection.root_ids)
        if intent.destination_root_id is not None and intent.destination_root_id not in root_ids:
            root_ids.append(intent.destination_root_id)
        policy = self._authorization.build_policy(tuple(root_ids))
        operations: list[PlannedFileOperation] = []

        if intent.requested_operation in {
            OperationType.RENAME_FILE,
            OperationType.RENAME_DIRECTORY,
        }:
            if intent.rename_rule is None:
                raise ValueError("Rename intents require a validated rename rule")
            operations.extend(self._compile_renames(policy, sources, intent.rename_rule))
        else:
            if intent.destination_root_id is None:
                raise ValueError("Move and organization intents require a destination root")
            destination_record = self._authorization.resolve_authorized(
                (intent.destination_root_id,)
            )[0]
            destination_base = destination_record.path
            for segment in intent.destination_subdirectory:
                PathPolicy.validate_windows_name(segment)
                destination_base /= segment
            operations.extend(
                self._compile_moves(
                    policy,
                    sources,
                    destination_base,
                    group_by=intent.group_by,
                )
            )

        if not operations:
            raise ValueError("The operation plan contains no effective change")
        if len(operations) > self._max_operations:
            raise ValueError(f"Operation plan exceeds the limit of {self._max_operations}")
        ordered = tuple(
            operation.model_copy(update={"sequence": index})
            for index, operation in enumerate(operations)
        )
        return FileOperationPlan(
            summary="在授权目录内安全移动或重命名文件",
            user_goal=user_goal,
            authorized_root_ids=tuple(root_ids),
            operations=ordered,
        )

    def compile_selected_move(
        self,
        user_goal: str,
        source_paths: tuple[Path, ...],
        destination_directory: Path,
        root_ids: tuple[UUID, ...],
    ) -> FileOperationPlan:
        """Compile a GUI-selected move without contacting any model."""
        if not source_paths:
            raise ValueError("Select at least one source object")
        policy = self._authorization.build_policy(root_ids)
        operations = self._compile_moves(policy, source_paths, destination_directory)
        if len(operations) > self._max_operations:
            raise ValueError(f"Operation plan exceeds the limit of {self._max_operations}")
        ordered = tuple(
            operation.model_copy(update={"sequence": index})
            for index, operation in enumerate(operations)
        )
        return FileOperationPlan(
            summary="移动已选择的文件或目录",
            user_goal=user_goal,
            authorized_root_ids=root_ids,
            operations=ordered,
        )

    def compile_selected_rename(
        self,
        user_goal: str,
        source_paths: tuple[Path, ...],
        rule: RenameRule,
        root_ids: tuple[UUID, ...],
    ) -> FileOperationPlan:
        """Compile a GUI-selected structured rename without contacting any model."""
        if not source_paths:
            raise ValueError("Select at least one source object")
        policy = self._authorization.build_policy(root_ids)
        operations = self._compile_renames(policy, source_paths, rule)
        if len(operations) > self._max_operations:
            raise ValueError(f"Operation plan exceeds the limit of {self._max_operations}")
        ordered = tuple(
            operation.model_copy(update={"sequence": index})
            for index, operation in enumerate(operations)
        )
        return FileOperationPlan(
            summary="按结构化规则重命名已选择的对象",
            user_goal=user_goal,
            authorized_root_ids=root_ids,
            operations=ordered,
        )

    def _compile_moves(
        self,
        policy: PathPolicy,
        sources: tuple[Path, ...],
        destination_base: Path,
        *,
        group_by: OrganizationGroup | None = None,
    ) -> list[PlannedFileOperation]:
        operations: list[PlannedFileOperation] = []
        planned_directories: set[Path] = set()
        states = [
            self._platform.inspect(policy.validate_operation_source(path)) for path in sources
        ]
        target_directories: set[Path] = {destination_base}
        if group_by is OrganizationGroup.MODIFIED_YEAR:
            target_directories.update(
                destination_base
                / str(datetime.fromtimestamp(state.modified_ns / 1_000_000_000, UTC).year)
                for state in states
            )
        for directory in sorted(target_directories, key=lambda path: (len(path.parts), str(path))):
            self._append_missing_directories(policy, directory, planned_directories, operations)

        for source_state in sorted(states, key=lambda value: str(value.path).casefold()):
            target_directory = destination_base
            if group_by is OrganizationGroup.MODIFIED_YEAR:
                year = datetime.fromtimestamp(source_state.modified_ns / 1_000_000_000, UTC).year
                target_directory /= str(year)
            destination = policy.validate_operation_destination(
                target_directory / source_state.path.name
            )
            operation_type = (
                OperationType.MOVE_FILE
                if source_state.kind is FileObjectKind.FILE
                else OperationType.MOVE_DIRECTORY
            )
            operations.append(
                PlannedFileOperation(
                    sequence=0,
                    operation_type=operation_type,
                    tool_name="file.move",
                    source=source_state.path,
                    destination=destination,
                    expected_source_state=source_state,
                )
            )
        return operations

    def _compile_renames(
        self,
        policy: PathPolicy,
        sources: tuple[Path, ...],
        rule: RenameRule,
    ) -> list[PlannedFileOperation]:
        operations: list[PlannedFileOperation] = []
        states = sorted(
            (self._platform.inspect(policy.validate_operation_source(path)) for path in sources),
            key=lambda value: str(value.path).casefold(),
        )
        for index, source_state in enumerate(states):
            new_name = self._apply_rename_rule(source_state, rule, index)
            PathPolicy.validate_windows_name(new_name)
            destination = policy.validate_rename_destination(
                source_state.path,
                source_state.path.with_name(new_name),
            )
            operation_id = uuid4()
            case_only = (
                source_state.path.name.casefold() == destination.name.casefold()
                and source_state.path.name != destination.name
            )
            temporary = (
                source_state.path.with_name(f".pc-manager-{operation_id.hex}.rename-tmp")
                if case_only
                else None
            )
            operation_type = (
                OperationType.RENAME_FILE
                if source_state.kind is FileObjectKind.FILE
                else OperationType.RENAME_DIRECTORY
            )
            operations.append(
                PlannedFileOperation(
                    operation_id=operation_id,
                    sequence=0,
                    operation_type=operation_type,
                    tool_name="file.rename",
                    source=source_state.path,
                    destination=destination,
                    expected_source_state=source_state,
                    internal_temporary_path=temporary,
                )
            )
        return operations

    @staticmethod
    def _append_missing_directories(
        policy: PathPolicy,
        destination: Path,
        planned: set[Path],
        operations: list[PlannedFileOperation],
    ) -> None:
        missing: list[Path] = []
        current = destination
        while not current.exists() and current not in planned:
            missing.append(current)
            current = current.parent
        for directory in reversed(missing):
            validated = policy.validate_operation_destination(directory)
            operations.append(
                PlannedFileOperation(
                    sequence=0,
                    operation_type=OperationType.CREATE_DIRECTORY,
                    tool_name="file.mkdir",
                    destination=validated,
                )
            )
            planned.add(validated)

    @staticmethod
    def _apply_rename_rule(state: FileState, rule: RenameRule, index: int) -> str:
        """Apply one finite literal transformation while preserving a file extension."""
        path = state.path
        suffix = path.suffix if state.kind is FileObjectKind.FILE else ""
        stem = path.name[: -len(suffix)] if suffix else path.name
        if rule.rule_type is RenameRuleType.PREFIX:
            result = f"{rule.value}{stem}"
        elif rule.rule_type is RenameRuleType.SUFFIX:
            result = f"{stem}{rule.value}"
        elif rule.rule_type is RenameRuleType.SEQUENCE:
            prefix = rule.value or "item_"
            result = f"{prefix}{rule.start + index:0{rule.width}d}"
        elif rule.rule_type is RenameRuleType.LOWERCASE:
            result = stem.lower()
        elif rule.rule_type is RenameRuleType.UPPERCASE:
            result = stem.upper()
        elif rule.rule_type is RenameRuleType.REPLACE_TEXT:
            if rule.value is None or rule.replacement is None:
                raise ValueError("Replace-text rules require both value and replacement")
            result = stem.replace(rule.value, rule.replacement)
        else:
            prefix = datetime.fromtimestamp(state.modified_ns / 1_000_000_000, UTC).strftime(
                "%Y-%m-%d_"
            )
            result = f"{prefix}{stem}"
        new_name = f"{result}{suffix}"
        if new_name == path.name:
            raise ValueError(f"Rename rule does not change the object name: {path.name}")
        return new_name


class FileOperationPlanner:
    """Request a finite provider intent after exact external-data consent."""

    def __init__(
        self,
        provider: LLMProvider,
        authorization: AuthorizedPathService,
        registry: ToolRegistry,
        external_consent: ExternalDataConsentService,
    ) -> None:
        self._provider = provider
        self._authorization = authorization
        self._registry = registry
        self._external_consent = external_consent

    def build_provider_request(self, user_goal: str) -> FileOperationPlannerRequest:
        """Build a path-free request containing only IDs, labels, and finite choices."""
        roots = self._authorization.list_authorized()
        if not roots:
            raise ValueError("Add authorized source and destination roots before planning")
        for tool_name in ("file.mkdir", "file.move", "file.rename"):
            try:
                self._registry.manifest(tool_name)
            except UnknownToolError as exc:
                raise ValueError(f"Required Stage 2A tool is unavailable: {tool_name}") from exc
        return FileOperationPlannerRequest(
            user_goal=user_goal,
            authorized_roots=tuple(
                AuthorizedRootOption(root_id=str(record.path_id), label=record.label)
                for record in roots
            ),
            allowed_operations=tuple(OperationType),
            allowed_rename_rules=tuple(RenameRuleType),
            allowed_grouping=tuple(OrganizationGroup),
            allowed_tools=("file.mkdir", "file.move", "file.rename"),
        )

    def request_external_consent(self, user_goal: str) -> ExternalDataConsentRequest:
        """Bind consent to the exact path-free Stage 2A provider payload."""
        request = self.build_provider_request(user_goal)
        return self._external_consent.request(
            purpose=ExternalDataPurpose.PLANNING,
            provider=self._provider.name,
            payload=request.model_dump(mode="json"),
            object_summary=(
                "将发送请求、授权目录标签和随机 ID；不会发送真实路径、文件名、"
                "文件内容、扫描记录或 Undo 数据。"
            ),
        )

    async def plan_intent(
        self,
        user_goal: str,
        confirmation_id: UUID,
    ) -> FileOperationIntentResult:
        """Return only a typed intent; concrete paths remain a local compiler decision."""
        request = self.build_provider_request(user_goal)
        payload = request.model_dump(mode="json")
        self._external_consent.require_approved(
            confirmation_id,
            purpose=ExternalDataPurpose.PLANNING,
            provider=self._provider.name,
            payload=payload,
        )
        result = await self._provider.create_file_operation_intent(request)
        return FileOperationIntentResult(
            intent=result.intent,
            provider=result.provider,
            provider_request_id=result.request_id,
        )
