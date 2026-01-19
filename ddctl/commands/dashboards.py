from __future__ import annotations

import typer
from rich.console import Console
from rich.json import JSON

from ..cli import get_client_from_ctx
from ..i18n import t

app = typer.Typer(help=t("Operaciones sobre Dashboards", "Dashboards operations"))
console = Console()


@app.command(
    "get",
    help=t(
        "GET /api/v1/dashboard/{id} y mostrar JSON",
        "GET /api/v1/dashboard/{id} and print JSON",
    ),
)
def get_dashboard(
    ctx: typer.Context,
    id: str = typer.Option(..., "--id", help=t("ID del dashboard", "Dashboard ID")),
) -> None:
    # Fetch dashboard by ID
    client = get_client_from_ctx(ctx)
    try:
        data = client.get(f"/api/v1/dashboard/{id}")
        console.print(JSON.from_data(data))
    except Exception as exc:
        raise typer.Exit(code=1) from exc

