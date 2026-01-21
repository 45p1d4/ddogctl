from __future__ import annotations

from ddctl.cli import app
from ddctl.checks.debug_help import find_missing_debug


def test_all_leaf_commands_have_debug() -> None:
    missing = find_missing_debug(app)
    assert missing == [], f"Missing --debug in: {missing}"

