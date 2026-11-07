"""
`ddogctl commands`: machine-readable catalog of every leaf command.

Built by walking the click command tree, so it can never drift from the real
CLI. Agents can bootstrap with `ddogctl --json commands` instead of scraping
`--help` output.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import click
import typer
from rich.console import Console

from ..i18n import t
from ..options import DebugOption
from ..ui import emit, new_table

console = Console()

# Commands that change state in the Datadog org. Everything else is read-only.
MUTATING_COMMANDS = {
    "monitors mute",
    "hosts mute",
    "hosts unmute",
    "downtimes schedule",
    "downtimes cancel",
    "incidents create",  # read-only with --dry-run
    "synthetics trigger",
    "services apply",
}

# Commands that never call the Datadog API.
OFFLINE_COMMANDS_PREFIXES = ("url ", "commands", "guaf")


def _first_line(text: Optional[str]) -> str:
    return (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""


def _param_type(p: click.Parameter) -> str:
    if isinstance(p, click.Option) and p.is_flag:
        return "flag"
    name = getattr(p.type, "name", None) or p.type.__class__.__name__
    return str(name).lower()


def _describe_option(p: click.Option) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "flags": [o for o in p.opts if o.startswith("-")],
        "type": _param_type(p),
        "required": bool(p.required),
    }
    if p.multiple:
        out["multiple"] = True
    default = p.default
    if not p.required and default not in (None, (), [], False) and not callable(default):
        out["default"] = default
    if p.help:
        out["help"] = _first_line(p.help)
    return out


def build_catalog(root: click.Command) -> Dict[str, Any]:
    commands: List[Dict[str, Any]] = []

    def _walk(cmd: click.Command, path: List[str]) -> None:
        if isinstance(cmd, click.Group):
            for name, sub in sorted(cmd.commands.items()):
                _walk(sub, [*path, name])
            return
        if not path:
            return
        full = " ".join(path)
        options = [
            _describe_option(p)
            for p in cmd.params
            if isinstance(p, click.Option) and not p.hidden and "--help" not in p.opts
        ]
        commands.append({
            "path": full,
            "help": _first_line(cmd.help or cmd.short_help),
            "mutating": full in MUTATING_COMMANDS,
            "offline": full.startswith(OFFLINE_COMMANDS_PREFIXES),
            "options": options,
        })

    _walk(root, [])
    global_opts = [
        _describe_option(p)
        for p in root.params
        if isinstance(p, click.Option) and "--help" not in p.opts
    ]
    return {"global_options": global_opts, "commands": commands}


def register(app: typer.Typer) -> None:
    @app.command(
        "commands",
        help=t(
            "Catálogo legible por máquinas de todos los comandos (usar con --json)",
            "Machine-readable catalog of all commands (use with --json)",
        ),
    )
    def commands_catalog(
        ctx: typer.Context,
        brief: bool = typer.Option(
            False, "--brief",
            help=t("Solo path, ayuda y si modifica estado", "Only path, help and whether it mutates"),
        ),
        debug: DebugOption = False,
    ) -> None:
        root = ctx.find_root().command
        catalog = build_catalog(root)
        cmds = catalog["commands"]
        if brief:
            cmds = [{k: c[k] for k in ("path", "help", "mutating")} for c in cmds]

        def _render() -> None:
            tbl = new_table("ddogctl commands")
            tbl.add_column("command", style="cyan", no_wrap=True)
            tbl.add_column("mutating", style="red", no_wrap=True)
            tbl.add_column("help", style="white")
            for c in cmds:
                tbl.add_row(c["path"], "yes" if c["mutating"] else "", c["help"])
            console.print(tbl)

        data: Any = cmds
        meta = {"count": len(cmds), "global_options": catalog["global_options"],
                "error_codes_doc": "ddctl/errors.py:ERROR_CODES"}
        emit(ctx, "commands", data, fields=None, meta=meta, table_renderer=_render)
