from herdr_master.core.available_models import AvailableModels
from herdr_master.core.config import AiHeadConfig
from herdr_master.pipeline.core_prompt import build_core_aihead_prompt


def test_core_prompt_contains_verified_delegation_and_limitations():
    prompt = build_core_aihead_prompt()
    for command in ("hc run --bg", "hc send", "hc get", "hc read", "hc wait"):
        assert command in prompt
    assert "$HC_TERMINAL_ID" in prompt
    assert "not established as Core-compatible" in prompt
    assert "not a guaranteed host background wakeup" in prompt
    assert "do not blindly resend" in prompt
    assert "herdr workspace" not in prompt
    assert "herdr agent" not in prompt
    assert "Software Architect and Workflow Orchestrator" in prompt


def test_core_prompt_preserves_config_and_task(tmp_path):
    lesson = tmp_path / "lesson.md"
    lesson.write_text("Prefer focused reviews.", encoding="utf-8")
    cfg = AiHeadConfig(
        soul="Custom soul", autonomy="routine", brief_before_action=True,
        lesson_paths=[str(lesson)],
    )
    available = AvailableModels(herdr={"pi": ["provider/model"], "claude": []})
    prompt = build_core_aihead_prompt(cfg, "  Fix the widget  ", "/project", True, available)
    for expected in ("Custom soul", "Working Directory: /project", "Fix the widget",
                     "automode = true", "herdr.pi: provider/model", "herdr.claude: (empty)",
                     "brief_before_action = true", "Prefer focused reviews."):
        assert expected in prompt


def test_core_prompt_empty_soul_and_task_use_defaults():
    prompt = build_core_aihead_prompt(AiHeadConfig(soul="  "), "  ")
    assert "Software Architect and Workflow Orchestrator" in prompt
    assert "Greet the user" in prompt
    assert "automode = false" in prompt
