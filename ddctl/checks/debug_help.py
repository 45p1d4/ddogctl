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

    Walks the click command tree directly. (Parsing `--help` text broke once
    Typer started rendering help inside Rich boxes: the "Commands:" header was
    never found, the inventory came back empty and the test passed vacuously.)
    """
    import click
    import typer

    root = typer.main.get_command(app)
    inventory: List[str] = []

    def _walk(cmd, path: List[str]) -> None:
        if isinstance(cmd, click.Group):
            for name, sub in sorted(cmd.commands.items()):
                _walk(sub, [*path, name])
        elif path:
            inventory.append(" ".join(path))

    _walk(root, [])
    return inventory


def find_missing_debug(app) -> List[str]:
    """
    Returns leaf command paths that do NOT declare a `--debug` option.
    """
    import click
    import typer

    root = typer.main.get_command(app)
    missing: List[str] = []
    for path in collect_inventory(app):
        cmd = root
        for name in path.split():
            cmd = cmd.commands[name]  # type: ignore[attr-defined]
        opts = {o for p in cmd.params if isinstance(p, click.Option) for o in p.opts}
        if "--debug" not in opts:
            missing.append(path)
    return missing
