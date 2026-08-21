from __future__ import annotations

import os

import pytest

from pc_manager_agent.platform_support.windows.msi_uninstall import WindowsMsiProductInventory


@pytest.mark.skipif(os.name != "nt", reason="Windows Installer API is Windows-only")
def test_unknown_synthetic_product_code_is_a_readonly_empty_query() -> None:
    inventory = WindowsMsiProductInventory()
    assert inventory.registrations("{F0000000-0000-0000-0000-00000000000A}") == ()
