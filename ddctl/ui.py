from __future__ import annotations

import json as _json
import sys
import traceback
from typing import Any, Callable, Dict, Iterable, List, NoReturn, Optional

import typer
from rich.table import Table
from rich import box

from .normalize import DEFAULT_FIELDS


def _format_meta_pair(key: str, value: Optional[str]) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    k = f"[dim]{key}[/dim]"
    v = f"[bold]{value}[/bold]"
    return f"{k}={v}"


def build_title(base: str, metadata: Dict[str, Optional[str]] | None = None) -> str:
    """
    Compose a consistent table title: 'Base  • key=value  • key=value'
    Keys dimmed, values bold. Empty/None entries are skipped.
    """
    if not metadata:
        return base
    parts = []
    for k in ["service", "env", "cluster", "from", "to", "date"]:
        if k in metadata:
            pair = _format_meta_pair(k, metadata.get(k))
            if pair:
                parts.append(pair)
    if not parts:
        return base
    sep = "  [dim]•[/dim]  "
    return f"{base}{sep}" + sep.join(parts)


def new_table(base_title: str, metadata: Dict[str, Optional[str]] | None = None) -> Table:
    """
    Create a Rich Table with consistent styling and full terminal width.
    """
    title = build_title(base_title, metadata or {})
    table = Table(
        title=title,
        show_lines=False,
        expand=True,
        box=box.SIMPLE_HEAD,
        pad_edge=False,
        show_header=True,
    )
    return table


def is_json(ctx: typer.Context) -> bool:
    return bool((ctx.obj or {}).get("json"))


def is_full(ctx: typer.Context) -> bool:
    return bool((ctx.obj or {}).get("full"))


def _whitelist(item: Any, fields: Iterable[str]) -> Any:
    if not isinstance(item, dict):
        return item
    fset = set(fields)
    return {k: v for k, v in item.items() if k in fset}


def emit(
    ctx: typer.Context,
    cmd: str,
    data: Any,
    *,
    raw: Any = None,
    fields: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
    table_renderer: Optional[Callable[[], None]] = None,
) -> None:
    """
    Centralized output helper.

    - `data` is the normalized (digested) shape — list of dicts or a single dict.
    - `raw`  is the original Datadog payload, emitted when `--full` is set.
      If `raw` is omitted, `--full` falls back to `data` (no regression vs the
      previous behavior, but also no escape hatch for that command).
    - When `--json` is active, emits compact JSON: {cmd, n, data, meta}.
      Without `--full`, fields are whitelisted via DEFAULT_FIELDS[cmd] (or the
      explicit `fields` argument).
    - When `--json` is NOT active, calls `table_renderer()` to keep the
      pre-existing Rich table behavior.
    """
    if is_json(ctx):
        if is_full(ctx):
            payload = raw if raw is not None else data
        else:
            payload = data
            wl = fields or DEFAULT_FIELDS.get(cmd)
            if wl:
                if isinstance(payload, list):
                    payload = [_whitelist(x, wl) for x in payload]
                elif isinstance(payload, dict):
                    payload = _whitelist(payload, wl)
        n = len(payload) if isinstance(payload, list) else 1
        out = {"cmd": cmd, "n": n, "data": payload}
        if meta:
            out["meta"] = meta
        sys.stdout.write(_json.dumps(out, separators=(",", ":"), default=str) + "\n")
        return
    if table_renderer is not None:
        table_renderer()


def emit_error(ctx: typer.Context, cmd: str, exc: BaseException) -> None:
    """
    When --json is active, write error JSON to stdout so agents can parse it:
    {"cmd", "error": {"code", "type", "message", "retryable", "status"?, "payload"?, "suggestions"?}}
    `code` is stable (see ddctl.errors.ERROR_CODES).
    """
    if not is_json(ctx):
        return
    from .errors import describe_error

    out = {"cmd": cmd, "error": describe_error(exc, cmd, _error_message(exc))}
    sys.stdout.write(_json.dumps(out, separators=(",", ":"), default=str) + "\n")


def _error_message(exc: BaseException) -> str:
    # click/typer usage errors (e.g. BadParameter) expose a clean message.
    fmt = getattr(exc, "format_message", None)
    if callable(fmt):
        try:
            return str(fmt())
        except Exception:
            pass
    return str(exc) or exc.__class__.__name__


def fail(ctx: typer.Context, cmd: str, exc: BaseException, debug: bool = False) -> NoReturn:
    """
    Report a command failure and exit with code 1.

    - With --json: a single `{cmd, error}` JSON line on stdout (parseable).
    - Without --json: a one-line error on stderr (never pollutes stdout).
    - With --debug: the full traceback on stderr as well.
    """
    if isinstance(exc, typer.Exit):
        raise exc
    if is_json(ctx):
        emit_error(ctx, cmd, exc)
    else:
        from .errors import describe_error

        info = describe_error(exc, cmd, _error_message(exc))
        sys.stderr.write(f"Error ({cmd}) [{info['code']}]: {info['message']}\n")
        for s in info.get("suggestions", [])[:2]:
            sys.stderr.write(f"  hint: {s}\n")
    if debug:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    sys.stderr.flush()
    raise typer.Exit(code=1) from exc


def cmd_name_from_ctx(ctx: typer.Context) -> str:
    """'ddogctl apm spans list' -> 'apm.spans.list'."""
    parts = (ctx.command_path or "").split()[1:]
    return ".".join(parts) or "ddogctl"

