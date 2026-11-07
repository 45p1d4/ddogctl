from __future__ import annotations

from ddctl.cli import app
from ddctl.checks.debug_help import collect_inventory, find_missing_debug


def test_inventory_is_not_empty() -> None:
    inventory = collect_inventory(app)
    # Guard against the check silently inspecting nothing.
    assert len(inventory) > 20, inventory
    assert "apm spans list" in inventory


def test_all_leaf_commands_have_debug() -> None:
    missing = find_missing_debug(app)
    assert missing == [], f"Missing --debug in: {missing}"
