from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel

from ..cli import get_client_from_ctx
from ..i18n import t

app = typer.Typer(help=t("Comandos de autenticación", "Authentication commands"))
console = Console()


@app.command(
    "status",
    help=t(
        "Llama a GET /api/v1/validate e imprime site y api_key_valid",
        "Calls GET /api/v1/validate and prints site and api_key_valid",
    ),
)
def status(ctx: typer.Context) -> None:
    # Keep runtime output minimal and language-neutral where possible
    client = get_client_from_ctx(ctx)
    try:
        data = client.get("/api/v1/validate")
        valid = bool(data.get("valid")) if isinstance(data, dict) else False
        console.print(
            Panel.fit(
                f"[bold]site[/bold]: {client.site}\n[bold]api_key_valid[/bold]: {valid}",
                title="ddogctl auth status",
            )
        )
    except Exception as exc:
        raise typer.Exit(code=1) from exc

