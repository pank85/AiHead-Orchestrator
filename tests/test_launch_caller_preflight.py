"""Exact caller preflight: implicit shells, agents and explicit failures."""
from unittest.mock import AsyncMock, Mock
import pytest
from herdr_master.herdr.socket import HerdrRPCError
from herdr_master.pipeline.parent_contract import classify_launch_caller, ParentRouteError

PANE = 'w2Q:p1'

@pytest.mark.asyncio
async def test_implicit_agent_and_explicit_stale_caller():
    client = Mock()
    client.get_agent = AsyncMock(return_value={'agent': {'pane_id': PANE, 'workspace_id': 'w2Q',
        'tab_id': 'w2Q:t1', 'agent': 'pi', 'agent_status': 'idle', 'terminal_id': 'term'}})
    parent, generation = await classify_launch_caller(client, caller_pane_id=PANE, parent_pane_id=None, delegated=False)
    assert parent == PANE and generation
    client.get_agent.side_effect = RuntimeError('unqueryable')
    with pytest.raises(ParentRouteError):
        await classify_launch_caller(client, caller_pane_id=PANE, parent_pane_id=PANE, delegated=True)
    with pytest.raises(ParentRouteError):
        await classify_launch_caller(client, caller_pane_id=None, parent_pane_id=None, delegated=True)

@pytest.mark.asyncio
async def test_implicit_shell_requires_positive_pane_evidence():
    client = Mock()
    client.get_agent = AsyncMock(side_effect=HerdrRPCError('agent_not_found', 'not found'))
    client.current_pane = AsyncMock(return_value={'type':'pane_info','pane':{'pane_id':PANE, 'agent':None}})
    assert await classify_launch_caller(client, caller_pane_id=PANE, parent_pane_id=None, delegated=False) == (None, None)
    client.current_pane.return_value = {'type':'pane_info','pane':{'pane_id':'w2Q:p2'}}
    with pytest.raises(ParentRouteError):
        await classify_launch_caller(client, caller_pane_id=PANE, parent_pane_id=None, delegated=False)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['agent', 'shell', 'unqueryable'])
async def test_allocation_classifies_before_any_pane_mutation(mode, monkeypatch, tmp_path):
    from pathlib import Path
    from types import SimpleNamespace
    from herdr_master.pipeline.launcher import _allocate_launch_target, HerdrContext

    monkeypatch.setenv('HERDR_PANE_ID', PANE)
    ctx = HerdrContext(session_name='s', socket_path=Path('/tmp/herdr.sock'), is_inside_herdr=True,
                       workspace_id='w2Q', tab_id='w2Q:t1', pane_id=PANE)
    client = SimpleNamespace(socket_path=Path('/tmp/herdr.sock'),
        get_agent=AsyncMock(), current_pane=AsyncMock(), create_tab=AsyncMock(
            return_value={'pane_id':'w2Q:p2', 'tab_id':'w2Q:t1'}), create_workspace=AsyncMock())
    if mode == 'agent':
        client.get_agent.return_value = {'agent': {'pane_id':PANE, 'workspace_id':'w2Q',
            'tab_id':'w2Q:t1', 'agent':'pi', 'agent_status':'idle', 'terminal_id':'term'}}
    elif mode == 'shell':
        client.get_agent.side_effect = HerdrRPCError('agent_not_found', 'not found')
        client.current_pane.return_value = {'type':'pane_info','pane':{'pane_id':PANE, 'agent':None}}
    else:
        client.get_agent.side_effect = RuntimeError('unavailable')
    kwargs = dict(session=None, isolated_name='isolated', tab_label='child',
                  workspace_label='child', cwd=str(tmp_path))
    if mode == 'unqueryable':
        with pytest.raises(ParentRouteError):
            await _allocate_launch_target(client, ctx, **kwargs)
        client.create_tab.assert_not_awaited()
        client.create_workspace.assert_not_awaited()
        return
    result = await _allocate_launch_target(client, ctx, **kwargs)
    client.create_tab.assert_awaited_once()
    assert result.delegated is (mode == 'agent')
    assert result.parent_pane_id == (PANE if mode == 'agent' else None)
    assert bool(result.parent_generation) is (mode == 'agent')
    assert client.create_tab.await_args.kwargs.get('env') == ({'HERDR_PARENT_PANE_ID': PANE} if mode == 'agent' else None)
