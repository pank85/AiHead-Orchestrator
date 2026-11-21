"""Real launcher/receipt adapters across a same-worker reassignment."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from herdr_master.pipeline import parent_contract as c
from herdr_master.pipeline.child_watch import ChildIdentity, read_herdr_children

PARENT, CHILD = 'w2Q:pE', 'w3H:pZ'
GEN = 'herdr-v1:["terminal",null]'


def test_reassignment_invalidates_old_receipt_and_resets_reconciliation(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    token = c._register_child(tmp_path, CHILD, 'worker', 'w3H:tZ', GEN,
                              parent=PARENT, parent_generation=GEN, socket_path=Path('/tmp/test.sock'))
    def rpc(endpoint, verb, pane, *args):
        workspace, ordinal = pane.split(':p')
        return {'type': 'agent_info' if verb == 'get' else 'agent_prompted',
                'agent': {'pane_id': pane, 'workspace_id': workspace,
                          'tab_id': f'{workspace}:t{ordinal}', 'agent': 'pi',
                          'agent_status': 'idle', 'terminal_id': 'terminal'}}
    mock = Mock(side_effect=rpc)
    monkeypatch.setattr(c, '_herdr_completion_rpc', mock)
    monkeypatch.setenv('HERDR_PANE_ID', CHILD)
    first = c.send_terminal_completion(tmp_path, token, 'first')
    with pytest.raises(RuntimeError, match='different message'):
        c.send_terminal_completion(tmp_path, token, 'different')
    payload = f'HERDR TERMINAL REPORT {first} from {CHILD}\n\nfirst'
    monkeypatch.setenv('HERDR_PANE_ID', PARENT)
    assert c.verify_terminal_completion(tmp_path, first, payload) == CHILD
    with pytest.raises(RuntimeError):
        c.verify_terminal_completion(tmp_path, first, payload + '!')
    ledger = tmp_path / 'children.tsv'
    ledger.write_text(ledger.read_text().replace('\trunning\t', '\treconciled\t'))
    kw = dict(parent_pane_id=PARENT, parent_generation=GEN, child_pane_id=CHILD, child_generation=GEN)
    old_epoch = c.current_assignment_epoch(tmp_path, **kw)
    token2 = c.assign_child(tmp_path, token)
    assert c.current_assignment_epoch(tmp_path, **kw) != old_epoch
    row = read_herdr_children(tmp_path, parent=ChildIdentity('herdr', PARENT, GEN))[0]
    assert not row.acknowledged and not row.completion_accepted
    assert '\trunning\t' in ledger.read_text()
    with pytest.raises(RuntimeError):
        c.verify_terminal_completion(tmp_path, first, payload)
    monkeypatch.setenv('HERDR_PANE_ID', CHILD)
    with pytest.raises(RuntimeError, match='stale assignment'):
        c.send_terminal_completion(tmp_path, token, 'first')
    second = c.send_terminal_completion(tmp_path, token2, 'fix completed')
    assert second != first and c.completion_receipt_accepted(tmp_path, **kw)
    assert sum(call.args[1] == 'prompt' for call in mock.call_args_list) == 2
