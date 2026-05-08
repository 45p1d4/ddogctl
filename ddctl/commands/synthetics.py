from __future__ import annotations

from typing import List

import typer
from rich.console import Console
from rich.json import JSON

from ..cli import get_client_from_ctx
from ..i18n import t
from ..options import DebugOption
from ..normalize import normalize_synthetic_trigger
from ..ui import emit

app = typer.Typer(help=t("Operaciones sobre Synthetics", "Synthetics operations"))
console = Console()


@app.command(
    "trigger",
    help=t(
        "POST /api/v1/synthetics/tests/trigger con body {\"tests\":[{\"public_id\":\"...\"}]}",
        "POST /api/v1/synthetics/tests/trigger with body {\"tests\":[{\"public_id\":\"...\"}]}",
    ),
)
def trigger_tests(
    ctx: typer.Context,
    public_id: List[str] = typer.Option(
        ..., "--public-id", help=t("Public ID del test (repetible)", "Public ID of the test (repeatable)"), show_default=False
    ),
    debug: DebugOption = False,
) -> None:
    # Trigger one or more synthetics tests by public ID
    client = get_client_from_ctx(ctx)
    try:
        tests = [{"public_id": pid} for pid in public_id]
        payload = {"tests": tests}
        with console.status("[dim]Ejecutando tests[/dim]"):
            data = client.post("/api/v1/synthetics/tests/trigger", json=payload) or {}
        normalized = normalize_synthetic_trigger(data)
        emit(
            ctx,
            "synthetics.trigger",
            normalized,
            raw=data,
            table_renderer=lambda: console.print(JSON.from_data(data)),
        )
    except Exception as exc:
        raise typer.Exit(code=1) from exc

