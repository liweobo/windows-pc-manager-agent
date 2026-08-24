"""Deterministic metadata-only classification for possible software residuals."""

from __future__ import annotations

from pathlib import Path

from pc_manager_agent.domain.software_residuals import (
    ResidualClassification,
    ResidualSource,
)

_DATABASE_SUFFIXES = frozenset({".db", ".db3", ".sqlite", ".sqlite3", ".mdb", ".accdb"})
_CONFIG_SUFFIXES = frozenset(
    {".cfg", ".conf", ".config", ".ini", ".json", ".toml", ".yaml", ".yml", ".xml"}
)
_LOG_SUFFIXES = frozenset({".log", ".etl"})
_TEMP_SUFFIXES = frozenset({".tmp", ".temp"})
_DUMP_SUFFIXES = frozenset({".dmp", ".mdmp"})


class ResidualClassifier:
    """Classify paths using finite source, suffix, and component rules only."""

    def classify(
        self,
        path: Path,
        source: ResidualSource,
        expected: ResidualClassification,
    ) -> tuple[ResidualClassification, tuple[str, ...]]:
        """Return a conservative category and stable reason codes."""
        suffix = path.suffix.casefold()
        components = {part.casefold() for part in path.parts}
        if source is ResidualSource.MSIX_PACKAGE_DATA:
            return ResidualClassification.PACKAGE_USER_DATA, ("source-msix-package-data",)
        if source is ResidualSource.SHORTCUT or suffix == ".lnk":
            return ResidualClassification.SHORTCUT, ("shortcut-metadata",)
        if suffix in _DATABASE_SUFFIXES:
            return ResidualClassification.DATABASE, ("database-suffix",)
        if components.intersection({"data", "database", "databases", "db", "pgdata"}):
            return ResidualClassification.DATABASE, ("database-or-data-component",)
        if suffix in _DUMP_SUFFIXES:
            return ResidualClassification.CRASH_DUMP, ("crash-dump-suffix",)
        if suffix in _LOG_SUFFIXES or "logs" in components or "log" in components:
            return ResidualClassification.LOG, ("log-name-or-suffix",)
        if suffix in _TEMP_SUFFIXES or "temp" in components or "tmp" in components:
            return ResidualClassification.TEMPORARY_DATA, ("temporary-name-or-suffix",)
        if "cache" in components or "caches" in components:
            return ResidualClassification.CACHE, ("cache-component",)
        if components.intersection({"plugins", "plugin", "extensions", "extension", "addons"}):
            return ResidualClassification.PLUGIN_OR_EXTENSION, ("plugin-component",)
        if components.intersection({"licenses", "license"}):
            return ResidualClassification.LICENSE_DATA, ("license-component",)
        if suffix in _CONFIG_SUFFIXES or components.intersection({"config", "settings"}):
            return ResidualClassification.CONFIGURATION, ("configuration-name-or-suffix",)
        if source is ResidualSource.KNOWN_APP_DATA:
            return ResidualClassification.USER_DATA, ("known-app-data-source",)
        return expected, (f"context-source-{source.value}",)
