"""Layout regressions for the compact cockpit at everyday terminal sizes."""

import pytest

from herdr_master.ui.tui.app import HerdrMasterApp
from herdr_master.ui.tui.widgets.dag import TaskDAGWidget
from herdr_master.ui.tui.widgets.pipeline_view import RoleCardWidget
from herdr_master.ui.tui.widgets.quick_launch import QuickLaunchBar


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (110, 32)])
async def test_compact_pipeline_layout(size):
    app = HerdrMasterApp(demo=True)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        toolbar = app.query_one(QuickLaunchBar)
        assert toolbar.size.height == 4
        descriptions = list(toolbar.query(".launch-description"))
        assert len(descriptions) == 2
        for description, button_id in zip(
            descriptions, ("#btn-new-master", "#btn-new-ergo"), strict=True
        ):
            button = toolbar.query_one(button_id)
            assert description.size.height == 1
            assert description.region.y == button.region.bottom
            assert description.region.bottom <= toolbar.region.bottom
        for button_id in ("#btn-new-master", "#btn-new-ergo", "#btn-settings"):
            button = app.query_one(button_id)
            assert button.region.y == toolbar.region.y
            assert button.region.right <= size[0]
            assert button.tooltip
        await pilot.press("3")
        await pilot.pause()
        assert max(card.size.height for card in app.query(RoleCardWidget)) <= 7
        # Pipeline details remain compact in their dedicated view.
        assert app.query_one(TaskDAGWidget).region.y < size[1] - 1
        for button_id in ("#btn-proceed", "#btn-edit-plan", "#btn-abort"):
            assert not app.query(button_id)
