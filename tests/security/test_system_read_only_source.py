from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.security
def test_stage3_production_sources_contain_no_mutation_calls() -> None:
    root = Path(__file__).parents[2] / "src" / "pc_manager_agent"
    sources = (
        root / "platform_support" / "windows" / "system_diagnostics.py",
        root / "tools" / "system_tools" / "collectors.py",
        root / "orchestration" / "system_diagnostics.py",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in sources).casefold()
    forbidden_calls = (
        ".kill(",
        ".terminate(",
        ".suspend(",
        ".resume(",
        "startservice(",
        "controlservice(",
        "changeserviceconfig(",
        "deleteservice(",
        "setvalue(",
        "deletevalue(",
        "createkey(",
        "deletekey(",
        "win32_product",
        "subprocess.",
    )
    assert not [item for item in forbidden_calls if item in combined]
