from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.json import JSON

from ..cli import get_client_from_ctx
from ..i18n import t
from ..options import DebugOption
from ..normalize import normalize_monitor
from ..ui import emit, new_table

app = typer.Typer(help=t("Operaciones sobre Monitors", "Monitors operations"))
console = Console()


@app.command(
    "list",
    help=t(
        "GET /api/v1/monitor y mostrar tabla: id, name, type, state",
        "GET /api/v1/monitor and render table: id, name, type, state",
    ),
)
def list_monitors(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(
        None, "--name", help=t("Filtro por nombre (substring)", "Name filter (substring)")
    ),
    tags: Optional[str] = typer.Option(
        None, "--tags",
        help=t("Filtro por scope tags coma-separados (p.ej. service:obe-api,env:prd)",
               "Comma-separated scope tags filter (e.g., service:obe-api,env:prd)"),
    ),
    monitor_tags: Optional[str] = typer.Option(
        None, "--monitor-tags",
        help=t("Filtro por monitor_tags (etiquetas del monitor)", "Filter by monitor_tags"),
    ),
    states: Optional[str] = typer.Option(
        None, "--states",
        help=t("Filtro client-side por estado coma-separado (Alert,Warn,No Data,OK)",
               "Client-side filter by overall_state, comma-separated (Alert,Warn,No Data,OK)"),
    ),
    page_size: int = typer.Option(
        100, "--page-size", help=t("Tamaño de página de la API", "API page size"), show_default=True,
    ),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        params = {"page_size": page_size}
        if tags:
            params["tags"] = tags
        if monitor_tags:
            params["monitor_tags"] = monitor_tags
        if name:
            params["name"] = name
        with console.status("[dim]Cargando monitores[/dim]"):
            items = client.get("/api/v1/monitor", params=params) or []
        if name:
            items = [m for m in items if name.lower() in (m.get("name", "") or "").lower()]
        if states:
            wanted = {s.strip() for s in states.split(",") if s.strip()}
            items = [m for m in items if (m.get("overall_state") or m.get("overallState") or "") in wanted]
        normalized = [normalize_monitor(m) for m in items]

        def _render() -> None:
            if debug:
                console.print(JSON.from_data(items))
                return
            table = new_table("Monitors")
            table.add_column("id", style="cyan", no_wrap=True)
            table.add_column("name", style="white")
            table.add_column("type", style="magenta", no_wrap=True)
            table.add_column("state", style="green", no_wrap=True)
            for m in items:
                mid = str(m.get("id", ""))
                mname = m.get("name", "") or ""
                mtype = m.get("type", "") or ""
                state = m.get("overall_state", "") or m.get("overallState", "") or ""
                table.add_row(mid, mname, mtype, state)
            console.print(table)

        emit(ctx, "monitors.list", normalized, table_renderer=_render)
    except Exception as exc:
        raise typer.Exit(code=1) from exc


@app.command(
    "mute",
    help=t(
        "POST /api/v1/monitor/{id}/mute y mostrar JSON de respuesta",
        "POST /api/v1/monitor/{id}/mute and print JSON response",
    ),
)
def mute_monitor(
    ctx: typer.Context,
    id: int = typer.Option(..., "--id", help=t("ID del monitor a silenciar", "Monitor ID to mute")),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        with console.status("[dim]Silenciando monitor[/dim]"):
            data = client.post(f"/api/v1/monitor/{id}/mute", json={}) or {}
        result = {
            "id": data.get("id", id),
            "name": data.get("name", ""),
            "muted": True,
        }
        emit(
            ctx,
            "monitors.mute",
            result,
            table_renderer=lambda: console.print(JSON.from_data(data)),
        )
    except Exception as exc:
        raise typer.Exit(code=1) from exc
