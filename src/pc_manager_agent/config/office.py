"""Hard-bounded Office budgets; changing them invalidates prepared work."""

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel


class OfficeLimits(FrozenModel):
    """Conservative limits independent from system-management settings."""

    max_files: int = Field(default=20, ge=1, le=20)
    max_total_bytes: int = Field(default=200 * 1024**2, ge=1, le=200 * 1024**2)
    parsed_cache_bytes: int = Field(default=32 * 1024**2, ge=1, le=64 * 1024**2)
    text_bytes: int = Field(default=10 * 1024**2, ge=1, le=10 * 1024**2)
    package_bytes: int = Field(default=25 * 1024**2, ge=1, le=25 * 1024**2)
    other_bytes: int = Field(default=50 * 1024**2, ge=1, le=50 * 1024**2)
    zip_entries: int = Field(default=10_000, ge=1, le=10_000)
    expanded_bytes: int = Field(default=200 * 1024**2, ge=1, le=200 * 1024**2)
    compression_ratio: int = Field(default=200, ge=1, le=200)
    max_cells: int = Field(default=200_000, ge=1, le=200_000)
    max_rows: int = Field(default=100_000, ge=1, le=100_000)
    max_sheets: int = Field(default=32, ge=1, le=32)
    max_pages: int = Field(default=300, ge=1, le=300)
    max_edit_cells: int = Field(default=10_000, ge=1, le=10_000)
    max_operations: int = Field(default=10_000, ge=1, le=10_000)
    max_text_chars: int = Field(default=2_000_000, ge=1, le=2_000_000)
    context_chars: int = Field(default=16_000, ge=100, le=16_000)
    chunk_chars: int = Field(default=2_000, ge=100, le=2_000)
    backup_bytes: int = Field(default=2 * 1024**3, ge=1, le=2 * 1024**3)
    preview_ttl_seconds: int = Field(default=300, ge=10, le=300)
    runtime_ttl_seconds: int = Field(default=60, ge=5, le=60)
    parse_timeout_seconds: float = Field(default=30, gt=0, le=60)
    parser_memory_bytes: int = Field(default=512 * 1024**2, ge=64 * 1024**2, le=1024**3)
    high_impact_files: int = Field(default=10, ge=1, le=10)
    high_impact_cells: int = Field(default=5_000, ge=1, le=5_000)
    high_impact_bytes: int = Field(default=50 * 1024**2, ge=1, le=50 * 1024**2)
