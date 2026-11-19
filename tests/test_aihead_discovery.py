from unittest.mock import AsyncMock, call

import pytest

from herdr_master.herdr.aihead_discovery import (
    AiHeadDiscoveryError,
    AiHeadLocation,
    discover_aihead,
    focus_aihead,
)
from herdr_master.herdr.snapshot import SessionSnapshot
from herdr_master.herdr.socket import HerdrConnectionError


def client_for(*, panes=(), agents=(), tabs=None, workspaces=None):
    client = AsyncMock()
    client.get_snapshot.return_value = SessionSnapshot(
        protocol=22,
        workspaces=workspaces or [{"workspace_id": "ws", "label": "project"}],
        tabs=tabs or [{"tab_id": "tab", "workspace_id": "ws", "label": "ai-head"}],
        panes=list(panes),
        agents=list(agents),
    )
    return client


@pytest.mark.asyncio
async def test_named_head_in_split_tab_and_focus_order():
    client = client_for(
        panes=[{"pane_id": "other", "tab_id": "tab"}, {"pane_id": "head", "tab_id": "tab"}],
        agents=[
            {"pane_id": "other", "name": "worker", "agent_status": "working"},
            {"pane_id": "head", "name": "ai-head", "agent_status": "working"},
        ],
        tabs=[{"tab_id": "tab", "workspace_id": "ws", "label": "shell"}],
    )
    location = await discover_aihead(client, workspace="project")
    assert location == AiHeadLocation("ws", "tab", "head")
    await focus_aihead(client, location)
    assert client.method_calls == [
        call.get_snapshot(), call.focus_workspace("ws"), call.focus_tab("tab"),
        call.focus_pane("head"),
    ]


@pytest.mark.asyncio
async def test_legacy_label_and_dead_agent():
    client = client_for(panes=[{"pane_id": "p", "tab_id": "tab", "agent_status": "idle"}])
    assert await discover_aihead(client) == AiHeadLocation("ws", "tab", "p")
    client.get_snapshot.return_value.panes[0].agent_status = "dead"
    assert await discover_aihead(client) is None
    assert await discover_aihead(client, workspace="missing") is None


@pytest.mark.asyncio
async def test_ambiguous_live_panes_are_not_guessed():
    client = client_for(panes=[
        {"pane_id": "a", "tab_id": "tab", "agent_status": "working"},
        {"pane_id": "b", "tab_id": "tab", "agent_status": "working"},
    ])
    with pytest.raises(AiHeadDiscoveryError, match="Multiple"):
        await discover_aihead(client)


@pytest.mark.asyncio
async def test_named_and_legacy_heads_are_ambiguous():
    client = client_for(
        panes=[
            {"pane_id": "named", "tab_id": "named-tab"},
            {"pane_id": "legacy", "tab_id": "legacy-tab", "agent_status": "working"},
        ],
        agents=[{"pane_id": "named", "name": "ai-head", "agent_status": "working"}],
        tabs=[
            {"tab_id": "named-tab", "workspace_id": "ws", "label": "shell"},
            {"tab_id": "legacy-tab", "workspace_id": "ws", "label": "ai-head"},
        ],
    )
    with pytest.raises(AiHeadDiscoveryError, match="Multiple"):
        await discover_aihead(client)


@pytest.mark.asyncio
async def test_incomplete_location_and_transport_errors_propagate():
    client = client_for(
        panes=[{"pane_id": "p", "agent_status": "working"}],
        agents=[{"pane_id": "p", "name": "ai-head", "agent_status": "working"}],
    )
    with pytest.raises(AiHeadDiscoveryError, match="Incomplete"):
        await discover_aihead(client)
    client.get_snapshot.side_effect = HerdrConnectionError("offline")
    with pytest.raises(HerdrConnectionError, match="offline"):
        await discover_aihead(client)


@pytest.mark.asyncio
async def test_focus_error_stops_sequence():
    client = AsyncMock()
    client.focus_tab.side_effect = HerdrConnectionError("gone")
    with pytest.raises(HerdrConnectionError, match="gone"):
        await focus_aihead(client, AiHeadLocation("ws", "tab", "p"))
    client.focus_pane.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_filter_before_ambiguity_and_agent_cwd(tmp_path):
    project = tmp_path / "project"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()
    (project / ".herdr-master.toml").touch()
    nested = project / "nested"
    nested.mkdir()
    client = client_for(
        panes=[
            {"pane_id": "other", "tab_id": "tab", "cwd": str(other)},
            {"pane_id": "head", "tab_id": "tab"},
        ],
        agents=[
            {"pane_id": "other", "name": "ai-head", "agent_status": "working"},
            {"pane_id": "head", "name": "ai-head", "agent_status": "working",
             "cwd": str(nested)},
        ],
    )
    assert await discover_aihead(client, project=project) == AiHeadLocation("ws", "tab", "head")
    with pytest.raises(AiHeadDiscoveryError, match="Multiple"):
        await discover_aihead(client)
    client.get_snapshot.return_value.panes[1].cwd = str(other)
    client.get_snapshot.return_value.agents[1].cwd = str(other)
    assert await discover_aihead(client, project=project) is None


@pytest.mark.asyncio
async def test_project_matching_multiple_and_explicit_workspace(tmp_path):
    client = client_for(
        panes=[
            {"pane_id": "a", "tab_id": "tab", "agent_status": "working", "cwd": str(tmp_path)},
            {"pane_id": "b", "tab_id": "tab", "agent_status": "working", "cwd": str(tmp_path)},
        ],
    )
    with pytest.raises(AiHeadDiscoveryError, match="Multiple"):
        await discover_aihead(client, project=tmp_path)
    assert await discover_aihead(client, workspace="missing", project=tmp_path) is None
    assert await discover_aihead(client, workspace="missing") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cwd", [None, "", "   ", "relative/path", "/bad\x00path"])
async def test_unknown_candidate_cwd_fails_closed_without_filesystem_for_malformed(
    tmp_path, monkeypatch, cwd
):
    client = client_for(panes=[
        {"pane_id": "p", "tab_id": "tab", "agent_status": "working", "cwd": cwd},
    ])
    # Resolve the valid project first, then reject the bad candidate before resolving it.
    from pathlib import Path

    original = Path.resolve
    calls = []

    def resolve(path, *args, **kwargs):
        calls.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(AiHeadDiscoveryError, match="Unknown project identity"):
        await discover_aihead(client, project=tmp_path)
    assert calls == [tmp_path]
    client.focus_pane.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_or_conflicting_candidate_prevents_reuse(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    client = client_for(
        panes=[
            {"pane_id": "a", "tab_id": "tab", "agent_status": "working", "cwd": str(tmp_path)},
            {"pane_id": "b", "tab_id": "tab", "agent_status": "working"},
        ],
    )
    with pytest.raises(AiHeadDiscoveryError, match="Unknown project identity"):
        await discover_aihead(client, project=tmp_path)
    client.get_snapshot.return_value.panes[1].cwd = str(tmp_path / "missing")
    with pytest.raises(AiHeadDiscoveryError, match="Unknown project identity"):
        await discover_aihead(client, project=tmp_path)
    client.get_snapshot.return_value.panes[1].cwd = str(tmp_path)
    client.get_snapshot.return_value.agents.append(
        {"pane_id": "b", "name": "ai-head", "agent_status": "working", "cwd": str(other)}
    )
    # Assignment validation is not enabled on snapshot models.
    from herdr_master.herdr.snapshot import AgentSnapshot

    client.get_snapshot.return_value.agents[-1] = AgentSnapshot.model_validate(
        client.get_snapshot.return_value.agents[-1]
    )
    with pytest.raises(AiHeadDiscoveryError, match="Conflicting project identity"):
        await discover_aihead(client, project=tmp_path)


@pytest.mark.asyncio
async def test_symlinks_nested_markers_and_exact_directory_fallback(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    child = project / "child"
    child.mkdir()
    link = tmp_path / "link"
    link.symlink_to(project, target_is_directory=True)
    client = client_for(panes=[
        {"pane_id": "p", "tab_id": "tab", "agent_status": "working", "cwd": str(link)},
    ])
    assert await discover_aihead(client, project=project) == AiHeadLocation("ws", "tab", "p")
    assert await discover_aihead(client, project=child) is None
    (project / ".herdr-master.toml").touch()
    assert await discover_aihead(client, project=child) == AiHeadLocation("ws", "tab", "p")
    (child / ".herdr-master.toml").touch()
    assert await discover_aihead(client, project=child) is None
