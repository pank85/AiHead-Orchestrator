"""Comprehensive test suite for HerdrMaster settings configuration and safe persistence."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from herdr_master.core.catalog import CatalogError, HarnessKind
from herdr_master.core.config import (
    DEFAULT_AI_HEAD_SOUL,
    DEFAULT_ORCHESTRATION_MODELS,
    AiHeadConfig,
    CouncilConfig,
    CouncilMember,
    FavoriteProject,
    GeneralConfig,
    HerdrMasterConfig,
    ModelsConfig,
    OrchestrationConfig,
    ProjectFavorite,
    ProjectsConfig,
    TUIConfig,
    WorktreeConfig,
    is_folder_available,
    load_project_config,
    resolve_effective_folder,
    resolve_folder_path,
    save_project_config,
    squad_from_project_config,
    squad_to_project_config,
)
from herdr_master.core.models import EffortLevel, HarnessBinding, Squad


def test_missing_file_defaults():
    """Missing or non-existent configuration file returns full defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        nonexistent = Path(tmpdir) / "does_not_exist" / ".herdr-master.toml"
        cfg = load_project_config(nonexistent)

        assert isinstance(cfg, HerdrMasterConfig)
        assert cfg.general.default_session == "default"
        assert cfg.general.target_integration_branch == "develop"
        assert cfg.worktree.base_directory == ".herdr-worktrees"
        assert cfg.tui.refresh_rate_ms == 250

        # Models: absent overrides
        assert cfg.models.default_squad is None
        assert cfg.models.worker_pool is None

        # Projects defaults
        assert cfg.projects.default_folder is None
        assert cfg.projects.favorites == []

        # Orchestration defaults
        assert cfg.orchestration.harness == HarnessKind.AGY
        assert cfg.orchestration.scout_model is None
        assert cfg.orchestration.architect_model is None
        assert cfg.orchestration.worker_models == []
        assert cfg.orchestration.reviewer_model is None
        assert cfg.orchestration.effective_scout_model() == "flash_lite"
        assert cfg.orchestration.effective_architect_model() == "pro"
        assert cfg.orchestration.effective_reviewer_model() == "pro"
        assert cfg.orchestration.effective_worker_models() == ["flash"]

        # Council defaults
        assert cfg.council.coordinator.kind == HarnessKind.PI
        assert cfg.council.coordinator.label == "coordinator"
        assert cfg.council.scout.kind == HarnessKind.AGY
        assert cfg.council.scout.label == "scout"
        assert len(cfg.council.members) == 2
        assert cfg.council.members[0].label == "council-1"
        assert cfg.council.members[0].enabled is True
        assert cfg.council.members[1].label == "council-2"
        assert cfg.council.members[1].enabled is True

        # AiHead defaults
        assert cfg.ai_head.enabled is False
        assert cfg.ai_head.soul == DEFAULT_AI_HEAD_SOUL
        assert cfg.ai_head.binding.kind == HarnessKind.PI
        assert cfg.ai_head.binding.model == "openai-codex/gpt-6.1-sol"
        assert cfg.ai_head.binding.label == "ai-head"
        assert cfg.ai_head.mode is None


def test_legacy_file_loading_preserves_new_section_defaults(tmp_path):
    """A legacy .herdr-master.toml lacking new sections loads cleanly with defaults."""
    legacy_toml = """
[general]
default_session = "custom-sess"
target_integration_branch = "main"

[worktree]
base_directory = ".custom-worktrees"

[tui]
theme = "nord"
"""
    config_file = tmp_path / ".herdr-master.toml"
    config_file.write_text(legacy_toml, encoding="utf-8")

    cfg = load_project_config(config_file)
    assert cfg.general.default_session == "custom-sess"
    assert cfg.general.target_integration_branch == "main"
    assert cfg.worktree.base_directory == ".custom-worktrees"
    assert cfg.tui.theme == "nord"

    # New sections defaulted
    assert cfg.projects.default_folder is None
    assert cfg.projects.favorites == []
    assert cfg.orchestration.harness == HarnessKind.AGY
    assert len(cfg.council.members) == 2
    assert cfg.ai_head.enabled is False


def test_projects_config_roundtrip_and_favorites(tmp_path):
    """ProjectsConfig supports default_folder and ordered named favorites."""
    cfg = HerdrMasterConfig()
    cfg.projects.default_folder = "/var/repos/main"
    cfg.projects.favorites = [
        FavoriteProject(name="Frontend", path="/var/repos/main/frontend"),
        FavoriteProject(name="Backend", path="/var/repos/main/backend"),
    ]

    save_path = tmp_path / ".herdr-master.toml"
    save_project_config(cfg, save_path)
    assert save_path.is_file()

    reloaded = load_project_config(save_path)
    assert reloaded.projects.default_folder == "/var/repos/main"
    assert len(reloaded.projects.favorites) == 2
    assert reloaded.projects.favorites[0].name == "Frontend"
    assert reloaded.projects.favorites[0].path == "/var/repos/main/frontend"
    assert reloaded.projects.favorites[1].name == "Backend"
    assert reloaded.projects.favorites[1].path == "/var/repos/main/backend"

    # ProjectFavorite alias works identically
    alias_fav = ProjectFavorite(name="Docs", path="/var/repos/main/docs")
    assert alias_fav.name == "Docs"


def test_projects_config_validation():
    """ProjectsConfig requires nonblank names and paths, and unique names."""
    # Blank name
    with pytest.raises(ValidationError, match="name must not be blank"):
        FavoriteProject(name="   ", path="/path/to/repo")

    # Blank path
    with pytest.raises(ValidationError, match="path must not be blank"):
        FavoriteProject(name="Repo", path="   ")

    # Duplicate names (case-insensitive)
    with pytest.raises(ValidationError, match="Duplicate favorite project name"):
        ProjectsConfig(
            favorites=[
                FavoriteProject(name="Web", path="/path/web1"),
                FavoriteProject(name="web", path="/path/web2"),
            ]
        )

    # Missing/removable directory does NOT make config unloadable
    proj = ProjectsConfig(
        default_folder="/nonexistent/mount/usb-drive",
        favorites=[FavoriteProject(name="Removable", path="/media/usb/project")],
    )
    assert proj.default_folder == "/nonexistent/mount/usb-drive"
    assert not is_folder_available(proj.favorites[0].path)


def test_orchestration_config_roundtrip_and_effective_models(tmp_path):
    """OrchestrationConfig supports single-harness setup, worker list, and effective resolution."""
    cfg = HerdrMasterConfig()
    cfg.orchestration.harness = HarnessKind.CLAUDE
    cfg.orchestration.scout_model = "claude-haiku"
    cfg.orchestration.architect_model = "claude-opus-custom"
    cfg.orchestration.worker_models = ["claude-sonnet-custom", "claude-3-opus"]
    cfg.orchestration.reviewer_model = "claude-opus-custom"

    save_path = tmp_path / ".herdr-master.toml"
    save_project_config(cfg, save_path)

    reloaded = load_project_config(save_path)
    assert reloaded.orchestration.harness == HarnessKind.CLAUDE
    assert reloaded.orchestration.scout_model == "claude-haiku"
    assert reloaded.orchestration.effective_scout_model() == "claude-haiku"
    assert reloaded.orchestration.effective_architect_model() == "claude-opus-custom"
    assert reloaded.orchestration.effective_reviewer_model() == "claude-opus-custom"
    assert reloaded.orchestration.effective_worker_models() == [
        "claude-sonnet-custom",
        "claude-3-opus",
    ]
    # workers property alias
    assert reloaded.orchestration.workers == [
        "claude-sonnet-custom",
        "claude-3-opus",
    ]


def test_orchestration_config_validation():
    """Orchestration rejects non-native harnesses and worker effort specifications."""
    # Harness lacking native subagent support (e.g. codex, gemini)
    with pytest.raises(ValidationError, match="does not support native orchestration"):
        OrchestrationConfig(harness="codex")  # type: ignore

    with pytest.raises(ValidationError, match="does not support native orchestration"):
        OrchestrationConfig(harness="gemini")  # type: ignore

    # Worker effort is forbidden (Architect decides worker effort)
    with pytest.raises(ValidationError, match="must not specify an effort level"):
        OrchestrationConfig(harness=HarnessKind.AGY, worker_models=["flash:high"])

    with pytest.raises(ValidationError, match="must not specify an effort level"):
        OrchestrationConfig(harness=HarnessKind.PI, worker_models=["xai/grok-4.7:max"])

    # Worker models without effort are allowed
    valid = OrchestrationConfig(
        harness=HarnessKind.PI,
        worker_models=["anthropic/claude-3-5-sonnet", "xai/grok-4.7"],
    )
    assert len(valid.worker_models) == 2


def test_orchestration_field_coercion_and_aliases():
    """OrchestrationConfig coerces coder_model pipe-separated strings and aliases."""
    cfg = OrchestrationConfig.model_validate(
        {
            "harness": "agy",
            "scout": "flash_lite",
            "architect": "pro",
            "coder_model": "flash | pro",
            "reviewer": "pro",
        }
    )
    assert cfg.scout_model == "flash_lite"
    assert cfg.architect_model == "pro"
    assert cfg.reviewer_model == "pro"
    assert cfg.worker_models == ["flash", "pro"]
    assert cfg.workers == ["flash", "pro"]

    # Setter test
    cfg.workers = ["gemini-3.8-flash"]
    assert cfg.worker_models == ["gemini-3.8-flash"]


def test_council_config_roundtrip(tmp_path):
    """CouncilConfig serializes coordinator, scout, and ordered member roster."""
    cfg = HerdrMasterConfig()
    cfg.council.coordinator = HarnessBinding.from_slug(
        "pi:openai-codex/gpt-6.1-sol:max", label="coordinator"
    )
    cfg.council.scout = HarnessBinding.from_slug(
        "agy:gemini-3.8-flash-medium:medium", label="scout"
    )
    cfg.council.members = [
        CouncilMember.from_slug(
            "pi:openai-codex/gpt-6.1-sol:max",
            label="alpha",
            description="Alpha planner",
            order=0,
        ),
        CouncilMember.from_slug(
            "pi:xai/grok-4.7:high",
            label="beta",
            description="Beta reviewer",
            order=1,
        ),
        CouncilMember.from_slug(
            "agy:gemini-3.8-flash-high:high",
            label="gamma",
            description="Gamma speed advocate",
            order=2,
        ),
    ]

    save_path = tmp_path / ".herdr-master.toml"
    save_project_config(cfg, save_path)

    reloaded = load_project_config(save_path)
    assert reloaded.council.coordinator.to_slug() == "pi:openai-codex/gpt-6.1-sol:max"
    assert reloaded.council.scout.to_slug() == "agy:gemini-3.8-flash-medium:medium"
    assert len(reloaded.council.members) == 3
    assert reloaded.council.members[0].label == "alpha"
    assert reloaded.council.members[1].label == "beta"
    assert reloaded.council.members[2].label == "gamma"
    assert reloaded.council.members[2].member_id == "gamma"


def test_council_config_members_may_share_model_but_not_identity():
    """Council members may use identical model bindings as long as identities are unique."""
    cfg = CouncilConfig(
        members=[
            CouncilMember.from_slug("pi:openai-codex/gpt-6.1-sol:max", label="member-1"),
            CouncilMember.from_slug("pi:openai-codex/gpt-6.1-sol:max", label="member-2"),
        ]
    )
    assert len(cfg.members) == 2
    assert cfg.members[0].model == cfg.members[1].model
    assert cfg.members[0].label != cfg.members[1].label


def test_council_invalid_roster_minimum_members():
    """Council rejects rosters with fewer than two enabled members."""
    # 0 members
    with pytest.raises(ValidationError, match="at least 2 enabled members"):
        CouncilConfig(members=[])

    # 1 member
    with pytest.raises(ValidationError, match="at least 2 enabled members"):
        CouncilConfig(
            members=[
                CouncilMember.from_slug("pi:openai-codex/gpt-6.1-sol:max", label="solo")
            ]
        )

    # 2 members, but one is disabled (1 enabled)
    with pytest.raises(ValidationError, match="at least 2 enabled members"):
        CouncilConfig(
            members=[
                CouncilMember.from_slug(
                    "pi:openai-codex/gpt-6.1-sol:max", label="m1", enabled=True
                ),
                CouncilMember.from_slug(
                    "pi:xai/grok-4.7:high", label="m2", enabled=False
                ),
            ]
        )


def test_council_invalid_roster_duplicate_or_blank_identities():
    """Council rejects duplicate member labels or blank member identities."""
    # Duplicate identities
    with pytest.raises(ValidationError, match="Duplicate Council member identity"):
        CouncilConfig(
            members=[
                CouncilMember.from_slug("pi:openai-codex/gpt-6.1-sol:max", label="lead"),
                CouncilMember.from_slug("pi:xai/grok-4.7:high", label="LEAD"),
            ]
        )

    # Blank identity
    with pytest.raises(ValidationError, match="non-blank identity"):
        CouncilConfig(
            members=[
                CouncilMember(kind=HarnessKind.PI, model="xai/grok-4.7", label="  "),
                CouncilMember(kind=HarnessKind.PI, model="xai/grok-4.7", label="m2"),
            ]
        )


def test_ai_head_config_disabled_by_default_and_roundtrip(tmp_path):
    """AiHead is disabled by default, retains persona and binding, and roundtrips."""
    cfg = HerdrMasterConfig()
    assert cfg.ai_head.enabled is False
    assert cfg.ai_head.soul == DEFAULT_AI_HEAD_SOUL
    assert cfg.ai_head.harness == HarnessKind.PI
    assert cfg.ai_head.model == "openai-codex/gpt-6.1-sol"

    # Explicit customization
    cfg.ai_head.enabled = True
    cfg.ai_head.soul = "Custom architect persona"
    cfg.ai_head.harness = HarnessKind.AGY
    cfg.ai_head.model = "gemini-3.8-flash-high"
    cfg.ai_head.effort = EffortLevel.HIGH
    cfg.ai_head.mode = "herdr"

    save_path = tmp_path / ".herdr-master.toml"
    save_project_config(cfg, save_path)

    reloaded = load_project_config(save_path)
    assert reloaded.ai_head.enabled is True
    assert reloaded.ai_head.soul == "Custom architect persona"
    assert reloaded.ai_head.harness == HarnessKind.AGY
    assert reloaded.ai_head.model == "gemini-3.8-flash-high"
    assert reloaded.ai_head.effort == EffortLevel.HIGH
    assert reloaded.ai_head.mode == "herdr"


def test_ai_head_unsupported_mode_rejected():
    """AiHead execution mode must be 'herdr' or 'cli'; others fail actionably."""
    with pytest.raises(ValidationError, match="Unsupported execution mode 'daemon'"):
        AiHeadConfig(mode="daemon")

    # Supported modes pass
    assert AiHeadConfig(mode="herdr").mode == "herdr"
    assert AiHeadConfig(mode="cli").mode == "cli"


def test_section_preservation_during_squad_update(tmp_path):
    """Updating squad via squad_to_project_config preserves all other settings and sections."""
    cfg = HerdrMasterConfig()
    cfg.general.default_session = "my-special-session"
    cfg.projects.default_folder = "/path/to/project"
    cfg.projects.favorites = [FavoriteProject(name="Fav", path="/path/to/project/fav")]
    cfg.orchestration.harness = HarnessKind.CLAUDE
    cfg.orchestration.scout_model = "claude-sonnet-custom"
    cfg.council.members = [
        CouncilMember.from_slug("pi:openai-codex/gpt-6.1-sol:max", label="m1"),
        CouncilMember.from_slug("pi:xai/grok-4.7:high", label="m2"),
        CouncilMember.from_slug("agy:gemini-3.8-flash-high:high", label="m3"),
    ]
    cfg.ai_head.enabled = True
    cfg.ai_head.soul = "Custom soul"

    save_path = tmp_path / ".herdr-master.toml"
    save_project_config(cfg, save_path)

    # Load existing config and update squad
    loaded = load_project_config(save_path)
    new_squad = Squad(
        scout=HarnessBinding.from_slug("agy:gemini-3.8-flash-high"),
        architect=HarnessBinding.from_slug("pi:openai-codex/gpt-6.1-sol:high"),
        reviewer=HarnessBinding.from_slug("pi:xai/grok-4.7:high"),
        worker_pool=[
            HarnessBinding.from_slug("agy:gemini-3.8-flash", label="w1"),
            HarnessBinding.from_slug("pi:xai/grok-4.7", label="w2"),
        ],
    )
    updated_cfg = squad_to_project_config(new_squad, existing_config=loaded)
    save_project_config(updated_cfg, save_path)

    # Verify all non-squad sections were preserved intact
    reloaded = load_project_config(save_path)
    assert reloaded.general.default_session == "my-special-session"
    assert reloaded.projects.default_folder == "/path/to/project"
    assert len(reloaded.projects.favorites) == 1
    assert reloaded.projects.favorites[0].name == "Fav"
    assert reloaded.orchestration.harness == HarnessKind.CLAUDE
    assert reloaded.orchestration.scout_model == "claude-sonnet-custom"
    assert len(reloaded.council.members) == 3
    assert reloaded.ai_head.enabled is True
    assert reloaded.ai_head.soul == "Custom soul"

    # And squad was updated
    reloaded_squad = squad_from_project_config(reloaded)
    assert reloaded_squad.scout.to_slug() == "agy:gemini-3.8-flash-high"
    assert len(reloaded_squad.worker_pool) == 2


def test_absent_vs_empty_worker_pool_semantics(tmp_path):
    """Absent worker_pool inherits catalog; empty worker_pool is explicit empty pool."""
    # 1. Absent
    cfg_absent = HerdrMasterConfig()
    save_path = tmp_path / ".herdr-master.toml"
    save_project_config(cfg_absent, save_path)

    loaded_absent = load_project_config(save_path)
    assert loaded_absent.models.worker_pool is None
    squad_absent = squad_from_project_config(loaded_absent)
    assert len(squad_absent.worker_pool) == 2  # inherited product squad

    # 2. Explicit empty
    cfg_empty = HerdrMasterConfig()
    cfg_empty.models.worker_pool = {}
    save_project_config(cfg_empty, save_path)

    loaded_empty = load_project_config(save_path)
    assert loaded_empty.models.worker_pool == {}
    squad_empty = squad_from_project_config(loaded_empty)
    assert len(squad_empty.worker_pool) == 0  # explicit empty pool


def test_atomic_write_failure_preserves_original_file(tmp_path):
    """If a write failure occurs during save, the original file is left intact."""
    config_file = tmp_path / ".herdr-master.toml"
    initial_content = '[general]\ndefault_session = "original_session"\n'
    config_file.write_text(initial_content, encoding="utf-8")

    cfg_new = HerdrMasterConfig()
    cfg_new.general.default_session = "modified_session"

    # Inject failure into os.replace
    with patch("os.replace", side_effect=OSError("Disk write error")):
        with pytest.raises(OSError, match="Disk write error"):
            save_project_config(cfg_new, config_file)

    # Original file is unmodified
    assert config_file.read_text(encoding="utf-8") == initial_content

    # No leftover temporary files
    tmp_files = list(tmp_path.glob(".*.tmp"))
    assert tmp_files == []


def test_malformed_file_raises_and_is_not_rewritten(tmp_path):
    """Malformed TOML or schema violations raise rather than returning defaults."""
    config_file = tmp_path / ".herdr-master.toml"

    # Syntax error
    config_file.write_text("this is completely invalid [[ TOML", encoding="utf-8")
    with pytest.raises(Exception):
        load_project_config(config_file)

    # Schema error (e.g. invalid council members)
    config_file.write_text(
        """
[council]
members = []
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_project_config(config_file)


def test_effective_folder_resolution_precedence(tmp_path):
    """Precedence: explicit launch folder > configured default > cwd."""
    explicit_dir = tmp_path / "explicit"
    default_dir = tmp_path / "default_dir"
    anchor_dir = tmp_path / "anchor"

    explicit_dir.mkdir()
    default_dir.mkdir()
    anchor_dir.mkdir()

    cfg = HerdrMasterConfig()
    cfg.projects.default_folder = str(default_dir)

    # 1. Explicit folder takes top priority
    res1 = resolve_effective_folder(
        explicit_folder=explicit_dir,
        config=cfg,
        anchor_dir=anchor_dir,
    )
    assert res1 == explicit_dir.resolve()

    # 2. Configured default applies when explicit is None or blank
    res2 = resolve_effective_folder(
        explicit_folder=None,
        config=cfg,
        anchor_dir=anchor_dir,
    )
    assert res2 == default_dir.resolve()

    res2_blank = resolve_effective_folder(
        explicit_folder="   ",
        config=cfg,
        anchor_dir=anchor_dir,
    )
    assert res2_blank == default_dir.resolve()

    # 3. Anchor dir applies when default_folder is also None
    cfg.projects.default_folder = None
    res3 = resolve_effective_folder(
        explicit_folder=None,
        config=cfg,
        anchor_dir=anchor_dir,
    )
    assert res3 == anchor_dir.resolve()


def test_resolve_folder_path_and_availability(tmp_path):
    """resolve_folder_path resolves relative to base_dir, and is_folder_available tests is_dir."""
    sub = tmp_path / "existing_sub"
    sub.mkdir()
    file_path = tmp_path / "regular_file.txt"
    file_path.write_text("hello", encoding="utf-8")

    assert is_folder_available(sub) is True
    assert is_folder_available(file_path) is False
    assert is_folder_available(tmp_path / "nonexistent") is False

    # Relative path resolution against base directory
    resolved = resolve_folder_path("existing_sub", base_dir=tmp_path)
    assert resolved == sub.resolve()
