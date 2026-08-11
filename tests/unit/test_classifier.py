from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.reports import FileCategory
from pc_manager_agent.tools.file_tools.classifier import FileTypeClassifier


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        ("photo.JPG", FileCategory.IMAGE),
        ("movie.mkv", FileCategory.VIDEO),
        ("sound.flac", FileCategory.AUDIO),
        ("notes.docx", FileCategory.DOCUMENT),
        ("manual.pdf", FileCategory.PDF),
        ("bundle.7z", FileCategory.ARCHIVE),
        ("setup.msi", FileCategory.INSTALLER),
        ("disk.vhdx", FileCategory.DISK_IMAGE),
        ("source.py", FileCategory.CODE),
        ("records.sqlite", FileCategory.DATABASE),
        ("snapshot.bak", FileCategory.BACKUP),
        ("unknown.custom", FileCategory.OTHER),
    ),
)
def test_classifier_uses_one_case_insensitive_mapping(
    name: str,
    expected: FileCategory,
) -> None:
    assert FileTypeClassifier().classify(Path(name)) is expected
