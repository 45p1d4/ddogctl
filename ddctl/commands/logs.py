from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ..cli import get_client_from_ctx
from ..utils_time import parse_time, to_iso8601
from ..i18n import t
from ..options import DebugOption
from rich.json import JSON as RichJSON

app = typer.Typer(help=t("Búsqueda de Logs", "Logs search"))
console = Console()


def _build_query(service: Optional[str], extra: Optional[str]) -> str:
    parts = []
    if service:
        parts.append(f"service:{service}")
    if extra:
        parts.append(extra)
    if not parts:
        return "*"
    return " ".join(parts)


@app.command(
    "query",
    help=t(
        "POST /api/v2/logs/events/search y mostrar tabla: timestamp, service, status, message",
        "POST /api/v2/logs/events/search and show table: timestamp, service, status, message",
    ),
)
def query_logs(
    ctx: typer.Context,
    from_: str = typer.Option(
        "-1h", "--from", help=t("Inicio del rango (relativo o ISO)", "Range start (relative or ISO)"), show_default=True
    ),
    to: str = typer.Option(
        "now", "--to", help=t("Fin del rango (relativo o ISO)", "Range end (relative or ISO)"), show_default=True
    ),
    service: Optional[str] = typer.Option(None, "--service", help=t("Filtrar por service", "Filter by service")),
    query: Optional[str] = typer.Option(None, "--query", help=t("Consulta adicional", "Additional query")),
    limit: int = typer.Option(50, "--limit", help=t("Límite de eventos", "Events limit"), show_default=True),
    debug: DebugOption = False,
) -> None:
    # Build and execute a logs search query over the provided time range
    client = get_client_from_ctx(ctx)
    try:
        dt_from = parse_time(from_)
        dt_to = parse_time(to)
        if dt_to < dt_from:
            raise typer.BadParameter(t("--to debe ser >= --from", "--to must be >= --from"))
        payload = {
            "filter": {
                "from": to_iso8601(dt_from),
                "to": to_iso8601(dt_to),
                "query": _build_query(service, query),
            },
            "page": {"limit": limit},
            "sort": "-timestamp",
        }
        data = client.post("/api/v2/logs/events/search", json=payload) or {}
        if debug:
            console.rule("logs search response")
            console.print(RichJSON.from_data(data))
            return
        items = data.get("data") or []
        table = Table(title="Logs", show_lines=False)
        table.add_column("timestamp", style="cyan", no_wrap=True)
        table.add_column("service", style="magenta", no_wrap=True)
        table.add_column("status", style="green", no_wrap=True)
        table.add_column("message", style="white")

        for item in items:
            attrs = (item or {}).get("attributes") or {}
            timestamp = attrs.get("timestamp", "") or ""
            # El 'service' puede venir en attributes.attributes.service o en attributes.service
            nested_attrs = attrs.get("attributes") or {}
            service_val = nested_attrs.get("service") or attrs.get("service") or ""
            status_val = attrs.get("status") or ""
            message_val = nested_attrs.get("message") or attrs.get("message") or ""
            if isinstance(message_val, (dict, list)):
                message_val = str(message_val)
            msg = (message_val or "")[:400]
            table.add_row(str(timestamp), str(service_val), str(status_val), msg)

        console.print(table)
    except Exception as exc:
        raise typer.Exit(code=1) from exc

