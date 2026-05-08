from __future__ import annotations

from typing import List, Optional

import typer
from rich.console import Console
from rich.json import JSON

from ..api import ApiError
from ..cli import get_client_from_ctx
from ..i18n import t
from ..options import DebugOption
from ..normalize import normalize_incident
from ..ui import emit, new_table

app = typer.Typer(help=t("Operaciones sobre Incidents", "Incidents operations"))
console = Console()


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------

@app.command(
    "create",
    help=t(
        "POST /api/v2/incidents — crear incidente con campos enriquecidos",
        "POST /api/v2/incidents — create an incident with rich fields",
    ),
)
def create_incident(
    ctx: typer.Context,
    title: str = typer.Option(..., "--title", help=t("Título del incidente", "Incident title")),
    severity: str = typer.Option(
        "SEV-3", "--severity",
        help=t("Severidad (UNKNOWN, SEV-1, SEV-2, SEV-3, SEV-4, SEV-5)",
               "Severity (UNKNOWN, SEV-1, SEV-2, SEV-3, SEV-4, SEV-5)"),
        show_default=True,
    ),
    summary: Optional[str] = typer.Option(
        None, "--summary",
        help=t("Resumen libre del incidente (markdown OK)", "Free-form incident summary (markdown OK)"),
    ),
    root_cause: Optional[str] = typer.Option(
        None, "--root-cause",
        help=t("Hipótesis o root cause confirmado", "Hypothesis or confirmed root cause"),
    ),
    detection_method: Optional[str] = typer.Option(
        None, "--detection-method",
        help=t("Cómo se detectó (p.ej. 'Datadog monitor', 'manual', 'customer report')",
               "How it was detected (e.g., 'Datadog monitor', 'manual', 'customer report')"),
    ),
    service: List[str] = typer.Option(
        None, "--service",
        help=t("Service afectado (repetible)", "Affected service (repeatable)"),
    ),
    team: List[str] = typer.Option(
        None, "--team",
        help=t("Team responsable (repetible)", "Responsible team (repeatable)"),
    ),
    customer_impact: bool = typer.Option(
        False, "--customer-impact",
        help=t("Marca el incidente como customer-impacting",
               "Flag the incident as customer-impacting"),
    ),
    customer_impact_scope: Optional[str] = typer.Option(
        None, "--customer-impact-scope",
        help=t("Descripción del scope de impacto al cliente",
               "Description of customer impact scope"),
    ),
    notification: List[str] = typer.Option(
        None, "--notification",
        help=t("Handle a notificar (repetible, p.ej. @oncall@example.com)",
               "Notification handle (repeatable, e.g., @oncall@example.com)"),
    ),
    debug: DebugOption = False,
) -> None:
    """
    Creates an incident with the most useful fields populated. Optional fields
    are only included when supplied so the API call stays minimal otherwise.
    """
    client = get_client_from_ctx(ctx)
    try:
        attrs: dict = {"title": title}

        # severity is set via the top-level attribute; the v2 API also accepts
        # it under fields, both shapes coexist in the wild.
        if severity:
            attrs["fields"] = attrs.get("fields") or {}
            attrs["fields"]["severity"] = {"type": "dropdown", "value": severity}

        # service/team are multi-select dropdowns inside fields
        if service:
            attrs.setdefault("fields", {})["services"] = {"type": "multiselect", "value": list(service)}
        if team:
            attrs.setdefault("fields", {})["teams"] = {"type": "multiselect", "value": list(team)}
        if detection_method:
            attrs.setdefault("fields", {})["detection_method"] = {"type": "dropdown", "value": detection_method}
        if root_cause:
            attrs.setdefault("fields", {})["root_cause"] = {"type": "textbox", "value": root_cause}
        if summary:
            attrs.setdefault("fields", {})["summary"] = {"type": "textbox", "value": summary}

        if customer_impact:
            attrs["customer_impacted"] = True
        if customer_impact_scope:
            attrs["customer_impact_scope"] = customer_impact_scope

        if notification:
            attrs["notification_handles"] = [{"display_name": h, "handle": h} for h in notification]

        payload = {"data": {"type": "incidents", "attributes": attrs}}
        with console.status("[dim]Creando incidente[/dim]"):
            data = client.post("/api/v2/incidents", json=payload) or {}
        item = (data.get("data") or {}) if isinstance(data, dict) else {}
        normalized = normalize_incident(item)
        emit(
            ctx,
            "incidents.create",
            normalized,
            raw=data,
            table_renderer=lambda: console.print(JSON.from_data(data)),
        )
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------

@app.command(
    "list",
    help=t(
        "GET /api/v2/incidents — listar incidentes recientes",
        "GET /api/v2/incidents — list recent incidents",
    ),
)
def list_incidents(
    ctx: typer.Context,
    state: Optional[str] = typer.Option(
        None, "--state",
        help=t("Filtro por state (active, stable, resolved)",
               "Filter by state (active, stable, resolved)"),
    ),
    page_size: int = typer.Option(
        20, "--page-size", help=t("Tamaño de página", "Page size"), show_default=True,
    ),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        params: dict = {"page[size]": page_size}
        if state:
            # filter in the response since the v2 list endpoint's query syntax
            # for state is inconsistent across versions
            pass
        with console.status("[dim]Listando incidentes[/dim]"):
            data = client.get("/api/v2/incidents", params=params) or {}
        items = data.get("data") or []
        if state:
            items = [i for i in items if (i.get("attributes") or {}).get("state") == state]
        normalized = [normalize_incident(i) for i in items]

        def _render() -> None:
            tbl = new_table("Incidents")
            tbl.add_column("id", style="cyan", no_wrap=True)
            tbl.add_column("severity", style="magenta", no_wrap=True)
            tbl.add_column("state", style="green", no_wrap=True)
            tbl.add_column("created", style="white", no_wrap=True)
            tbl.add_column("title", style="white")
            for r in normalized:
                tbl.add_row(
                    str(r.get("id") or ""),
                    str(r.get("severity") or ""),
                    str(r.get("state") or ""),
                    str(r.get("created") or ""),
                    str(r.get("title") or ""),
                )
            console.print(tbl)

        emit(
            ctx,
            "incidents.list",
            normalized,
            raw=data,
            meta={"state": state, "count": len(items)},
            table_renderer=_render,
        )
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


# --------------------------------------------------------------------------
# get
# --------------------------------------------------------------------------

@app.command(
    "get",
    help=t(
        "GET /api/v2/incidents/{id} — detalle de un incidente",
        "GET /api/v2/incidents/{id} — detail of one incident",
    ),
)
def get_incident(
    ctx: typer.Context,
    id: str = typer.Option(..., "--id", help=t("ID del incidente", "Incident ID")),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        with console.status("[dim]Obteniendo incidente[/dim]"):
            data = client.get(f"/api/v2/incidents/{id}") or {}
        item = (data.get("data") or {}) if isinstance(data, dict) else {}
        normalized = normalize_incident(item)
        emit(
            ctx,
            "incidents.get",
            normalized,
            raw=data,
            table_renderer=lambda: console.print(JSON.from_data(data)),
        )
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc
