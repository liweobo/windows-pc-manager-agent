from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.browser.protocol import BrowserWorkerRequest
from pc_manager_agent.domain.browser import BrowserActionKind, BrowserActionRequest
from pc_manager_agent.safety.browser.network import BrowserNetworkPolicyError, BrowserUrlPolicy


class _Resolver:
    def __init__(self, address: str) -> None:
        self.address = address

    def resolve(self, hostname: str) -> tuple[str, ...]:
        return (self.address,)


@pytest.mark.security
def test_cloud_metadata_and_dns_rebinding_addresses_are_blocked() -> None:
    for address in ("169.254.169.254", "127.0.0.1", "10.0.0.7", "::1"):
        with pytest.raises(BrowserNetworkPolicyError):
            BrowserUrlPolicy(_Resolver(address)).validate("https://public.example/")


@pytest.mark.security
def test_protocol_and_action_models_reject_shell_script_selector_and_upload_fields() -> None:
    with pytest.raises(ValidationError):
        BrowserWorkerRequest.model_validate(
            {"command": "PERFORM", "action_json": "{}", "executable": "cmd.exe"}
        )
    action = BrowserActionRequest(session_id=uuid4(), kind=BrowserActionKind.OBSERVE)
    for name in ("selector", "javascript", "upload_path", "command", "cookies"):
        with pytest.raises(ValidationError):
            BrowserActionRequest.model_validate(
                {**action.model_dump(mode="json"), name: "forbidden"}
            )


@pytest.mark.security
def test_production_browser_code_has_no_arbitrary_page_eval_or_shell_fallback() -> None:
    root = Path(__file__).parents[2] / "src" / "pc_manager_agent" / "browser"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "page.evaluate(" not in source
    assert "shell=True" not in source
    assert "powershell" not in source.casefold()
    assert "cmd.exe" not in source.casefold()
    assert "connect_over_cdp" not in source
    assert "storage_state=" not in source
