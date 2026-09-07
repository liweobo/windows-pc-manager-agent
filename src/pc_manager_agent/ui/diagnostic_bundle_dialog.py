"""Explicit reviewed diagnostic-bundle export shared by normal and safe-mode windows."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from pc_manager_agent.diagnostics.bundle import DiagnosticBundleError, build_runtime_metadata

if TYPE_CHECKING:
    from pc_manager_agent.app.runtime import ApplicationRuntime


def export_diagnostic_bundle(parent: QWidget, runtime: ApplicationRuntime) -> None:
    """Show exact local bundle scope, require one R1 review, then export without upload."""
    selected, _selected_filter = QFileDialog.getSaveFileName(
        parent,
        "导出已脱敏诊断包",
        "pc-manager-diagnostics.zip",
        "ZIP 文件 (*.zip)",
    )
    if not selected:
        return
    try:
        preview = runtime.diagnostic_bundle.prepare(
            Path(selected),
            build_runtime_metadata(
                runtime.settings,
                runtime.migration_report,
                frozen_binary=bool(getattr(sys, "frozen", False)),
            ),
            _recent_error_codes(runtime),
        )
    except DiagnosticBundleError as exc:
        QMessageBox.warning(parent, "未导出", f"诊断包 Preview 无法建立：{exc}")
        return
    entries = "\n".join(f"- {entry.name}（{entry.size_bytes} 字节）" for entry in preview.entries)
    exclusions = "\n".join(f"- {item}" for item in preview.exclusions)
    decision = QMessageBox.question(
        parent,
        "确认导出本地诊断包（R1）",
        f"目标：{preview.output_path}\n\n包含：\n{entries}\n\n明确不包含：\n{exclusions}\n\n"
        "不会自动上传；恢复等级 MANUAL。是否创建此文件？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if decision is not QMessageBox.StandardButton.Yes:
        try:
            runtime.diagnostic_bundle.reject(preview)
        except DiagnosticBundleError:
            QMessageBox.warning(parent, "审计失败", "拒绝结果未能安全记录；没有创建诊断包。")
        return
    try:
        authority = runtime.diagnostic_bundle.approve(preview)
        result = runtime.diagnostic_bundle.export(authority)
    except DiagnosticBundleError as exc:
        QMessageBox.warning(parent, "未导出", f"诊断包未创建：{exc}")
        return
    QMessageBox.information(
        parent,
        "诊断包已创建",
        f"已在本地创建 {result.output_path.name}（{result.size_bytes} 字节）。不会自动上传。",
    )


def _recent_error_codes(runtime: ApplicationRuntime) -> tuple[str, ...]:
    codes: list[str] = []
    for row in runtime.audit.list_recent(100):
        if row.error is None:
            continue
        code = row.event_type.upper().replace(".", "_").replace("-", "_")
        if code and len(code) <= 80 and code not in codes:
            codes.append(code)
        if len(codes) == 50:
            break
    return tuple(codes)
