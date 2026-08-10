"""Central extension-based file categorization for every Stage 1 component."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pc_manager_agent.domain.reports import FileCategory


class FileTypeClassifier:
    """Classify a path using one conservative, centrally maintained mapping."""

    _CATEGORIES: ClassVar[dict[FileCategory, frozenset[str]]] = {
        FileCategory.IMAGE: frozenset(
            {
                ".bmp",
                ".gif",
                ".heic",
                ".jpeg",
                ".jpg",
                ".png",
                ".raw",
                ".svg",
                ".tif",
                ".tiff",
                ".webp",
            }
        ),
        FileCategory.VIDEO: frozenset(
            {".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm", ".wmv"}
        ),
        FileCategory.AUDIO: frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".wma"}),
        FileCategory.DOCUMENT: frozenset(
            {
                ".csv",
                ".doc",
                ".docx",
                ".epub",
                ".md",
                ".odt",
                ".ppt",
                ".pptx",
                ".rtf",
                ".txt",
                ".xls",
                ".xlsx",
            }
        ),
        FileCategory.PDF: frozenset({".pdf"}),
        FileCategory.ARCHIVE: frozenset(
            {".7z", ".bz2", ".gz", ".rar", ".tar", ".tgz", ".xz", ".zip"}
        ),
        FileCategory.INSTALLER: frozenset({".appx", ".exe", ".msi", ".msix", ".msixbundle"}),
        FileCategory.DISK_IMAGE: frozenset({".img", ".iso", ".vhd", ".vhdx"}),
        FileCategory.CODE: frozenset(
            {
                ".c",
                ".cpp",
                ".cs",
                ".css",
                ".go",
                ".h",
                ".html",
                ".java",
                ".js",
                ".json",
                ".jsx",
                ".php",
                ".ps1",
                ".py",
                ".rs",
                ".sql",
                ".toml",
                ".ts",
                ".tsx",
                ".xml",
                ".yaml",
                ".yml",
            }
        ),
        FileCategory.DATABASE: frozenset({".db", ".db3", ".mdb", ".sqlite", ".sqlite3"}),
        FileCategory.BACKUP: frozenset({".bak", ".backup", ".bkp", ".old"}),
    }

    def classify(self, path: Path) -> FileCategory:
        """Return a stable category without inspecting file contents."""
        extension = path.suffix.casefold()
        for category, extensions in self._CATEGORIES.items():
            if extension in extensions:
                return category
        return FileCategory.OTHER
