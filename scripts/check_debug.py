from __future__ import annotations

import sys

from ddctl.cli import app
from ddctl.checks.debug_help import collect_inventory, find_missing_debug


def main() -> int:
    inventory = collect_inventory(app)
    print("Leaf command inventory:")
    for cmd in inventory:
        print(f"  - {cmd}")
    missing = find_missing_debug(app)
    if missing:
        print("\nCommands missing --debug in help:")
        for cmd in missing:
            print(f"  - {cmd}")
        return 1
    print("\nAll leaf commands include --debug in --help.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

