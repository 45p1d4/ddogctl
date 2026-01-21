from __future__ import annotations

from typing import List

from typer.testing import CliRunner


def _invoke_help(app, path: List[str]) -> str:
    runner = CliRunner()
    result = runner.invoke(app, [*path, "--help"])
    return result.output or ""


def _parse_subcommands(help_text: str) -> List[str]:
    lines = help_text.splitlines()
    subcommands: List[str] = []
    in_commands = False
    for line in lines:
        if not in_commands:
            if line.strip().lower().startswith("commands:"):
                in_commands = True
            continue
        # End of commands section when hitting another header or blank line without indentation
        if not line.strip():
            # blank line => likely end of section
            if subcommands:
                break
            else:
                continue
        # Click formats subcommands like: "  name  Description..."
        if line.startswith("  "):
            # take the first token after leading spaces
            name = line.strip().split()[0]
            if name not in subcommands:
                subcommands.append(name)
            continue
        # Non-indented line while parsing commands means next section
        break
    return subcommands


def _is_leaf(app, path: List[str]) -> bool:
    help_text = _invoke_help(app, path)
    subs = _parse_subcommands(help_text)
    return len(subs) == 0


def collect_inventory(app) -> List[str]:
    """
    Returns full command paths (space-separated) for all leaf commands.
    Example: ["apm spans list", "apm errors rate", "metrics query", ...]
    """
    inventory: List[str] = []

    def _walk(path: List[str]) -> None:
        help_text = _invoke_help(app, path)
        subs = _parse_subcommands(help_text)
        if not subs:
            inventory.append(" ".join(path).strip())
            return
        for name in subs:
            _walk([*path, name])

    _walk([])
    # Filter out the empty root entry if present
    return [p for p in inventory if p]


def find_missing_debug(app) -> List[str]:
    """
    Returns leaf command paths whose --help output does NOT contain '--debug'.
    """
    missing: List[str] = []
    runner = CliRunner()
    for path in collect_inventory(app):
        args = path.split() + ["--help"]
        result = runner.invoke(app, args)
        output = result.output or ""
        if "--debug" not in output:
            missing.append(path)
    return missing

