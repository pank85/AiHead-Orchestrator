"""Approval provenance and safe Antigravity startup, without live agents."""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from herdr_master.core.config import AiHeadConfig
from herdr_master.core.models import HarnessBinding, HarnessKind
from herdr_master.pipeline.ai_head import (
    AiHeadDecisionRequest,
    build_ai_head_system_prompt,
    build_headless_cli_argv,
    build_interactive_ai_head_prompt,
    execute_cli_decision,
)
from herdr_master.pipeline.parent_contract import (
    UntrustedWorkspaceError,
    compose_first_prompt,
    require_existing_agy_trust,
)


def agy_config():
    return AiHeadConfig(
        enabled=True,
        dangerously_skip_permissions=True,
        binding=HarnessBinding(kind=HarnessKind.AGY, model="gemini-2.5-pro", label="ai-head"),
    )


@pytest.mark.parametrize("builder", [build_interactive_ai_head_prompt, build_ai_head_system_prompt])
def test_automode_separates_routine_work_from_gates(builder):
    prompt = builder(automode=True)
    for expected in ("autonomously", "one plan checkpoint", "rationale", "options",
                     "impact", "non-automode", "explicitly gated", "least-privilege"):
        assert expected.lower() in prompt.lower()
    assert "actual user reply" in prompt
    assert "no" in prompt.lower()


def test_first_prompt_separates_plan_authority_from_specific_hard_stop():
    prompt = compose_first_prompt("task", parent_pane_id="w3H:pZ")
    assert "Only AiHead or the human user may explicitly approve a specific gated plan" in prompt
    assert "Coordinator self-approval" in prompt and "not plan approval" in prompt
    assert "explicit user approval for the specific action and target" in prompt
    assert "Technical assent, plan approval, configuration flags, silence, timeout, and trust dialogs are not user approval" in prompt
    assert "On denial or timeout, leave the restricted state unchanged" in prompt


def test_interactive_user_tab_requires_real_correlated_user_reply():
    prompt = build_interactive_ai_head_prompt()
    assert "Only an intentional root opens user tabs" in prompt
    assert "--focus" in prompt and "Leave that tab open" in prompt
    assert "is not proof of user origin" in prompt
    assert "pending question before forwarding to the exact waiting child" in prompt
    assert "without verified provenance, request confirmation" in prompt


def test_existing_approval_is_read_only_and_workspace_specific(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    settings = tmp_path / ".gemini/antigravity-cli/settings.json"
    settings.parent.mkdir(parents=True)
    trusted = tmp_path / "existing"
    payload = json.dumps({"trustedWorkspaces": [str(trusted)], "userSetting": "unchanged"}).encode()
    settings.write_bytes(payload)
    require_existing_agy_trust(str(trusted))
    with pytest.raises(UntrustedWorkspaceError) as exc:
        require_existing_agy_trust(str(tmp_path / "another"), parent_pane_id="w3H:pZ")
    assert 'herdr agent prompt w3H:pZ "<message>"' in str(exc.value)
    assert settings.read_bytes() == payload


@pytest.mark.asyncio
async def test_untrusted_native_request_returns_needs_user_without_runner_or_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    request = AiHeadDecisionRequest(question="How should this proceed?", origin_cwd=str(tmp_path))
    runner = AsyncMock()
    reply = await execute_cli_decision(agy_config(), request, runner=runner)
    assert reply.request_id == request.request_id and reply.status == "needs_user"
    assert reply.decision is None and "no agent started" in reply.rationale
    runner.assert_not_called()
    assert not (tmp_path / ".gemini").exists()
    assert "--dangerously-skip-permissions" not in build_headless_cli_argv(agy_config(), "prompt")
