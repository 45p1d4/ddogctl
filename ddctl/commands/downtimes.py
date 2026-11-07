from __future__ import annotations

from typing import List, Optional

import typer
from rich.console import Console
from rich.json import JSON as RichJSON

from ..cli import get_client_from_ctx
from ..api import ApiError
from ..i18n import t
from ..options import DebugOption
from ..normalize import normalize_downtime, trunc
from ..ui import emit, new_table, fail

app = typer.Typer(help=t("Operaciones sobre Downtimes", "Downtimes operations"))
console = Console()


@app.command(
    "list",
    help=t(
        "GET /api/v2/downtime — listar downtimes",
        "GET /api/v2/downtime — list downtimes",
    ),
)
def list_downtimes(
    ctx: typer.Context,
    current_only: bool = typer.Option(False, "--current-only", help=t("Solo downtimes activos", "Active only")),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        params = {}
        if current_only:
            params["current_only"] = "true"
        with console.status("[dim]Cargando downtimes[/dim]"):
            resp = client.get("/api/v2/downtime", params=params) or {}
        items = resp.get("data") or []
        normalized = [normalize_downtime(it) for it in items]

        def _render() -> None:
            if debug:
                console.print(RichJSON.from_data(resp))
                return
            table = new_table("Downtimes")
            table.add_column("id", style="cyan", no_wrap=True)
            table.add_column("scope", style="magenta")
            table.add_column("active", style="green", no_wrap=True)
            table.add_column("start", style="white", no_wrap=True)
            table.add_column("end", style="white", no_wrap=True)
            table.add_column("message", style="white")
            for r in normalized:
                table.add_row(
                    str(r.get("id") or ""),
                    str(r.get("scope") or ""),
                    ("" if r.get("active") is None else str(r.get("active"))),
                    str(r.get("start") or ""),
                    str(r.get("end") or ""),
                    str(r.get("message") or ""),
                )
            console.print(table)

        emit(ctx, "downtimes.list", normalized, raw=resp, table_renderer=_render)
    except Exception as exc:
        fail(ctx, "downtimes.list", exc, debug)


@app.command(
    "schedule",
    help=t(
        "POST /api/v2/downtime — programar un downtime",
        "POST /api/v2/downtime — schedule a downtime",
    ),
)
def schedule_downtime(
    ctx: typer.Context,
    scope: str = typer.Option(..., "--scope", help=t("Scope del downtime (p.ej. env:prd OR cluster:tor)", "Downtime scope (e.g., env:prd OR cluster:tor)")),
    start: Optional[str] = typer.Option(None, "--start", help=t("ISO datetime de inicio", "Start ISO datetime")),
    end: Optional[str] = typer.Option(None, "--end", help=t("ISO datetime de fin", "End ISO datetime")),
    message: Optional[str] = typer.Option(None, "--message", help=t("Razón del downtime", "Downtime message")),
    monitor_id: Optional[int] = typer.Option(
        None, "--monitor-id",
        help=t("Silenciar solo este monitor (por defecto: todos los monitores del scope)",
               "Mute only this monitor (default: all monitors in scope)"),
    ),
    monitor_tag: List[str] = typer.Option(
        None, "--monitor-tag",
        help=t("Silenciar monitores con este tag (repetible; por defecto '*')",
               "Mute monitors with this tag (repeatable; default '*')"),
    ),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        # `scope` selects the *groups/hosts* being muted; `monitor_identifier`
        # selects *which monitors*. Previously the scope string was reused as a
        # monitor tag, so the downtime only matched monitors literally tagged
        # e.g. "env:prd OR cluster:tor" (usually none). ['*'] = all monitors.
        if monitor_id is not None:
            identifier: dict = {"monitor_id": monitor_id}
        else:
            identifier = {"monitor_tags": list(monitor_tag) if monitor_tag else ["*"]}
        attributes: dict = {
            "monitor_identifier": identifier,
            "scope": scope,
        }
        schedule = {k: v for k, v in (("start", start), ("end", end)) if v}
        if schedule:
            attributes["schedule"] = schedule
        if message:
            attributes["message"] = message
        body = {"data": {"type": "downtime", "attributes": attributes}}
        with console.status("[dim]Programando downtime[/dim]"):
            resp = client.post("/api/v2/downtime", json=body) or {}
        item = resp.get("data") or {}
        normalized = normalize_downtime(item)
        emit(ctx, "downtimes.schedule", normalized, raw=resp, table_renderer=lambda: console.print(RichJSON.from_data(resp)))
    except Exception as exc:
        fail(ctx, "downtimes.schedule", exc, debug)


@app.command(
    "cancel",
    help=t(
        "DELETE /api/v2/downtime/{id} — cancelar un downtime",
        "DELETE /api/v2/downtime/{id} — cancel a downtime",
    ),
)
def cancel_downtime(
    ctx: typer.Context,
    id: str = typer.Option(..., "--id", help=t("Downtime ID", "Downtime ID")),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        with console.status("[dim]Cancelando downtime[/dim]"):
            client.request("DELETE", f"/api/v2/downtime/{id}")
        result = {"id": id, "cancelled": True}
        emit(ctx, "downtimes.cancel", result, table_renderer=lambda: console.print(f"Downtime {id} cancelado."))
    except Exception as exc:
        fail(ctx, "downtimes.cancel", exc, debug)
