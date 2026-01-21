from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.json import JSON

from ..cli import get_client_from_ctx
from ..i18n import t
from ..options import DebugOption

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
    debug: DebugOption = False,
) -> None:
    # Render list of monitors with optional substring filter
    client = get_client_from_ctx(ctx)
    try:
        items = client.get("/api/v1/monitor") or []
        if name:
            items = [m for m in items if name.lower() in (m.get("name", "") or "").lower()]
        if debug:
            console.print(JSON.from_data(items))
            return
        table = Table(title="Monitors", show_lines=False)
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
    # Mute a specific monitor by ID
    client = get_client_from_ctx(ctx)
    try:
        data = client.post(f"/api/v1/monitor/{id}/mute", json={})
        console.print(JSON.from_data(data))
    except Exception as exc:
        raise typer.Exit(code=1) from exc

