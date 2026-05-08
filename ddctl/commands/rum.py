from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.json import JSON as RichJSON

from ..cli import get_client_from_ctx
from ..api import ApiError
from ..i18n import t
from ..options import DebugOption
from ..utils_time import parse_time, to_iso8601
from ..normalize import bucket_count, extract_buckets, normalize_rum_application, normalize_rum_event, trunc
from ..ui import emit, new_table

app = typer.Typer(help=t("Operaciones de RUM (Real User Monitoring)", "RUM (Real User Monitoring) operations"))
console = Console()

apps_app = typer.Typer(help=t("RUM Applications", "RUM Applications"))
events_app = typer.Typer(help=t("RUM Events", "RUM Events"))
page_app = typer.Typer(help=t("RUM Page metrics", "RUM Page metrics"))
app.add_typer(apps_app, name="apps")
app.add_typer(events_app, name="events")
app.add_typer(page_app, name="page")


@apps_app.command(
    "list",
    help=t("GET /api/v2/rum/applications — listar apps RUM", "GET /api/v2/rum/applications — list RUM apps"),
)
def apps_list(
    ctx: typer.Context,
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        with console.status("[dim]Cargando RUM apps[/dim]"):
            resp = client.get("/api/v2/rum/applications") or {}
        items = resp.get("data") or []
        normalized = [normalize_rum_application(it) for it in items]

        def _render() -> None:
            table = new_table("RUM Applications")
            table.add_column("id", style="cyan", no_wrap=True)
            table.add_column("name", style="white")
            table.add_column("type", style="magenta", no_wrap=True)
            for r in normalized:
                table.add_row(str(r.get("id") or ""), str(r.get("name") or ""), str(r.get("type") or ""))
            console.print(table)

        emit(ctx, "rum.apps.list", normalized, table_renderer=_render)
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


@events_app.command(
    "search",
    help=t(
        "POST /api/v2/rum/events/search — buscar eventos RUM",
        "POST /api/v2/rum/events/search — search RUM events",
    ),
)
def events_search(
    ctx: typer.Context,
    query: Optional[str] = typer.Option(None, "--query", help=t("Consulta RUM (p.ej. @type:error)", "RUM query (e.g., @type:error)")),
    application_id: Optional[str] = typer.Option(None, "--application-id", help="RUM application ID"),
    from_: str = typer.Option("now-1h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    limit: int = typer.Option(10, "--limit", help="Limit", show_default=True),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        dt_from = parse_time(from_)
        dt_to = parse_time(to)
        if dt_to < dt_from:
            raise typer.BadParameter(t("--to debe ser >= --from", "--to must be >= --from"))
        q_parts = []
        if application_id:
            q_parts.append(f"@application.id:{application_id}")
        if query:
            q_parts.append(query)
        effective_query = " ".join(q_parts) if q_parts else "*"
        payload = {
            "filter": {
                "from": to_iso8601(dt_from),
                "to": to_iso8601(dt_to),
                "query": effective_query,
            },
            "page": {"limit": limit},
            "sort": "-timestamp",
        }
        with console.status("[dim]Buscando eventos RUM[/dim]"):
            resp = client.post("/api/v2/rum/events/search", json=payload) or {}
        items = resp.get("data") or []
        normalized = [normalize_rum_event(it) for it in items]

        def _render() -> None:
            if debug:
                console.print(RichJSON.from_data(resp))
                return
            table = new_table("RUM Events", {"from": from_, "to": to})
            table.add_column("ts", style="cyan", no_wrap=True)
            table.add_column("type", style="magenta", no_wrap=True)
            table.add_column("view_url", style="white")
            table.add_column("error_msg", style="red")
            for r in normalized:
                table.add_row(
                    str(r.get("ts") or ""),
                    str(r.get("type") or ""),
                    str(r.get("view_url") or ""),
                    str(r.get("error_msg") or ""),
                )
            console.print(table)

        meta = {"from": from_, "to": to, "query": effective_query}
        emit(ctx, "rum.events.search", normalized, meta=meta, table_renderer=_render)
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


@events_app.command(
    "count",
    help=t(
        "POST /api/v2/rum/analytics/aggregate — contar eventos agrupados",
        "POST /api/v2/rum/analytics/aggregate — count grouped events",
    ),
)
def events_count(
    ctx: typer.Context,
    query: Optional[str] = typer.Option(None, "--query", help="RUM query"),
    group_by: str = typer.Option("@type", "--group-by", help=t("Facet a agrupar (p.ej. @type, @view.url)", "Facet to group by (e.g., @type, @view.url)"), show_default=True),
    from_: str = typer.Option("now-1h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    limit: int = typer.Option(10, "--limit", help="Limit", show_default=True),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        dt_from = parse_time(from_)
        dt_to = parse_time(to)
        body = {
            "filter": {
                "from": to_iso8601(dt_from),
                "to": to_iso8601(dt_to),
                "query": query or "*",
            },
            "compute": [{"aggregation": "count"}],
            "group_by": [
                {
                    "facet": group_by,
                    "limit": limit,
                    "sort": {"type": "measure", "aggregation": "count", "order": "desc"},
                }
            ],
        }
        with console.status("[dim]Calculando agregados RUM[/dim]"):
            resp = client.post("/api/v2/rum/analytics/aggregate", json=body) or {}
        buckets = extract_buckets(resp)
        rows = []
        for b in buckets:
            ref = b.get("attributes") or b
            key = (ref.get("by") or {}).get(group_by, "")
            rows.append({"group": trunc(key, 80), "count": bucket_count(b)})

        def _render() -> None:
            if debug:
                console.print(RichJSON.from_data(resp))
                return
            table = new_table(f"RUM count by {group_by}", {"from": from_, "to": to})
            table.add_column(group_by, style="magenta")
            table.add_column("count", style="cyan", no_wrap=True)
            for r in rows:
                table.add_row(str(r["group"]), str(r["count"]))
            console.print(table)

        meta = {"from": from_, "to": to, "group_by": group_by, "query": query or "*"}
        emit(ctx, "rum.events.count", rows, meta=meta, table_renderer=_render)
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc
