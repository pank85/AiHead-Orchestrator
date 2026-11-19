from hashlib import sha256
from unittest.mock import AsyncMock, patch

import pytest

from herdr_master.herdr.core_aihead import CoreAiHeadError, ensure_core_aihead


@pytest.fixture
def core():
    module = "herdr_master.herdr.core_aihead"
    with (patch(f"{module}.core_client.terminal_list", new_callable=AsyncMock) as listing,
          patch(f"{module}.core_client.request", new_callable=AsyncMock) as request,
          patch(f"{module}.which", return_value=None) as which):
        listing.return_value = {"terminals": []}
        yield listing, request, which


def terminal(project):
    digest = sha256(str(project.resolve()).encode()).hexdigest()
    return {"name": f"aihead-{digest[:24]}", "labels": {
        "aihead.role": "head", "aihead.project": digest},
        "address": "existing", "state": "live"}


async def test_reuse_does_not_check_launch_support_or_executable(tmp_path, core):
    listing, request, which = core
    listing.return_value = {"terminals": [terminal(tmp_path)]}
    result = await ensure_core_aihead(backend="core", cwd=tmp_path, kind="claude", effort="high")
    assert (result.address, result.created, result.prompt_status) == ("existing", False, "not_sent")
    which.assert_not_called()
    request.assert_not_awaited()
    with pytest.raises(CoreAiHeadError, match="task was not sent"):
        await ensure_core_aihead(backend="core", cwd=tmp_path, kind="claude", task="hello")


@pytest.mark.parametrize("scenario", ["conflict", "duplicate"])
async def test_identity_errors_precede_launch_checks(tmp_path, core, scenario):
    listing, request, which = core
    item = terminal(tmp_path)
    listing.return_value = {"terminals": ([{**item, "labels": {}}] if scenario == "conflict"
                                          else [item, item])}
    with pytest.raises(CoreAiHeadError, match="conflicting|multiple"):
        await ensure_core_aihead(backend="core", cwd=tmp_path, kind="claude")
    which.assert_not_called()
    request.assert_not_awaited()


@pytest.mark.parametrize("kind", ["claude", "agy", "opencode", "hermes", "kilo"])
@pytest.mark.parametrize("effort", [None, "high"])
async def test_unverified_launches_fail_closed(tmp_path, core, kind, effort):
    listing, request, which = core
    with pytest.raises(CoreAiHeadError, match="unsupported Core harness.*only pi"):
        await ensure_core_aihead(backend="core", cwd=tmp_path, kind=kind, effort=effort)
    listing.assert_awaited_once()
    which.assert_not_called()
    request.assert_not_awaited()


async def test_missing_pi_executable_fails_after_discovery(tmp_path, core):
    listing, request, _ = core
    with pytest.raises(CoreAiHeadError, match="pi executable not found"):
        await ensure_core_aihead(backend="core", cwd=tmp_path, kind="pi")
    listing.assert_awaited_once()
    request.assert_not_awaited()
