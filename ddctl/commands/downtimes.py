from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.json import JSON as RichJSON

from ..cli import get_client_from_ctx
from ..api import ApiError
from ..i18n import t
from ..options import DebugOption
from ..normalize import normalize_downtime, trunc
from ..ui import emit, new_table

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
                    str(r.get("active") or ""),
                    str(r.get("start") or ""),
                    str(r.get("end") or ""),
                    str(r.get("message") or ""),
                )
            console.print(table)

        emit(ctx, "downtimes.list", normalized, raw=resp, table_renderer=_render)
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


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
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        attributes = {
            "monitor_identifier": {"monitor_tags": [scope]},
            "scope": scope,
            "schedule": {
                "start": start,
                "end": end,
            },
        }
        if message:
            attributes["message"] = message
        body = {"data": {"type": "downtime", "attributes": attributes}}
        with console.status("[dim]Programando downtime[/dim]"):
            resp = client.post("/api/v2/downtime", json=body) or {}
        item = resp.get("data") or {}
        normalized = normalize_downtime(item)
        emit(ctx, "downtimes.schedule", normalized, raw=resp, table_renderer=lambda: console.print(RichJSON.from_data(resp)))
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


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
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc
