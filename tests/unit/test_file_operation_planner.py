from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.file_operations import (
    FileOperationIntentDraft,
    FileSelectionRule,
    OperationType,
    OrganizationGroup,
    RenameRule,
    RenameRuleType,
)
from pc_manager_agent.orchestration.file_operation_planner import FileOperationSourceResolver


def test_source_resolver_uses_authorized_ids_extensions_recursion_and_limit(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    nested = root / "nested"
    nested.mkdir(parents=True)
    pdf = nested / "report.PDF"
    pdf.write_text("pdf", encoding="utf-8")
    (nested / "note.txt").write_text("note", encoding="utf-8")
    record = runtime.authorized_paths.add_authorized(root, label="Downloads")
    recursive = FileSelectionRule(root_ids=(record.path_id,), extensions=("pdf",))
    resolver = FileOperationSourceResolver(runtime.authorized_paths, max_sources=2)
    assert resolver.resolve(recursive) == (pdf.resolve(),)
    assert resolver.resolve(recursive.model_copy(update={"recursive": False})) == ()
    with pytest.raises(ValueError, match="Record-only"):
        resolver.resolve(FileSelectionRule(root_ids=(record.path_id,), record_ids=(1,)))
    (root / "second.pdf").write_text("two", encoding="utf-8")
    (root / "third.pdf").write_text("three", encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds"):
        resolver.resolve(recursive)


@pytest.mark.windows
def test_compiler_groups_by_local_modified_year_and_creates_missing_directories(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "Downloads"
    destination_root = tmp_path / "Documents"
    source_root.mkdir()
    destination_root.mkdir()
    source = source_root / "report.pdf"
    source.write_text("report", encoding="utf-8")
    timestamp = datetime(2024, 6, 1).timestamp()
    os.utime(source, (timestamp, timestamp))
    source_record = runtime.authorized_paths.add_authorized(source_root, label="Downloads")
    destination_record = runtime.authorized_paths.add_authorized(
        destination_root, label="Documents"
    )
    services = runtime.create_file_operation_services()
    intent = FileOperationIntentDraft(
        selection=FileSelectionRule(
            root_ids=(source_record.path_id,),
            extensions=(".pdf",),
        ),
        destination_root_id=destination_record.path_id,
        destination_subdirectory=("PDF",),
        group_by=OrganizationGroup.MODIFIED_YEAR,
        requested_operation=OperationType.MOVE_FILE,
    )
    plan = services.compiler.compile("organize PDFs", intent, (source,))
    assert [item.operation_type for item in plan.operations] == [
        OperationType.CREATE_DIRECTORY,
        OperationType.CREATE_DIRECTORY,
        OperationType.MOVE_FILE,
    ]
    assert plan.operations[-1].destination == destination_root / "PDF" / "2024" / source.name


@pytest.mark.windows
@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (RenameRule(rule_type=RenameRuleType.PREFIX, value="pre_"), "pre_Report.txt"),
        (RenameRule(rule_type=RenameRuleType.SUFFIX, value="_done"), "Report_done.txt"),
        (RenameRule(rule_type=RenameRuleType.LOWERCASE), "report.txt"),
        (RenameRule(rule_type=RenameRuleType.UPPERCASE), "REPORT.txt"),
        (
            RenameRule(
                rule_type=RenameRuleType.REPLACE_TEXT,
                value="Report",
                replacement="Invoice",
            ),
            "Invoice.txt",
        ),
    ],
)
def test_compiler_applies_only_finite_rename_rules(
    runtime: ApplicationRuntime,
    tmp_path: Path,
    rule: RenameRule,
    expected: str,
) -> None:
    root = tmp_path / rule.rule_type.value
    root.mkdir()
    source = root / "Report.txt"
    source.write_text("report", encoding="utf-8")
    record = runtime.authorized_paths.add_authorized(root)
    plan = runtime.create_file_operation_services().compiler.compile_selected_rename(
        "rename",
        (source,),
        rule,
        (record.path_id,),
    )
    assert plan.operations[0].destination.name == expected


@pytest.mark.windows
def test_compiler_rejects_noop_and_operation_limit(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    first = root / "a.txt"
    second = root / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    record = runtime.authorized_paths.add_authorized(root)
    services = runtime.create_file_operation_services()
    with pytest.raises(ValueError, match="does not change"):
        services.compiler.compile_selected_rename(
            "lowercase",
            (first,),
            RenameRule(rule_type=RenameRuleType.LOWERCASE),
            (record.path_id,),
        )
    limited = type(services.compiler)(
        runtime.authorized_paths,
        runtime.file_operation_platform,
        max_operations=1,
    )
    with pytest.raises(ValueError, match="limit"):
        limited.compile_selected_rename(
            "prefix two",
            (first, second),
            RenameRule(rule_type=RenameRuleType.PREFIX, value="x_"),
            (record.path_id,),
        )
