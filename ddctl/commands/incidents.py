from __future__ import annotations

import typer
from rich.console import Console
from rich.json import JSON

from ..cli import get_client_from_ctx
from ..i18n import t
from ..options import DebugOption

app = typer.Typer(help=t("Operaciones sobre Incidents", "Incidents operations"))
console = Console()


@app.command(
    "create",
    help=t(
        "POST /api/v2/incidents con data.type=incidents, attributes.title y attributes.severity",
        "POST /api/v2/incidents with data.type=incidents, attributes.title and attributes.severity",
    ),
)
def create_incident(
    ctx: typer.Context,
    title: str = typer.Option(..., "--title", help=t("Título del incidente", "Incident title")),
    severity: str = typer.Option("SEV-2", "--severity", help=t("Severidad (p. ej. SEV-1, SEV-2)", "Severity (e.g., SEV-1, SEV-2)")),
    debug: DebugOption = False,
) -> None:
    # Create an incident with the given title and severity
    client = get_client_from_ctx(ctx)
    try:
        payload = {
            "data": {
                "type": "incidents",
                "attributes": {
                    "title": title,
                    "severity": severity,
                },
            }
        }
        data = client.post("/api/v2/incidents", json=payload)
        console.print(JSON.from_data(data))
    except Exception as exc:
        raise typer.Exit(code=1) from exc

