from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
import yaml
from rich.console import Console
from rich.json import JSON as RichJSON
from rich.table import Table

from ..cli import get_client_from_ctx
from ..i18n import t
from ..api import ApiError

app = typer.Typer(help=t("Service Definitions (create/update/delete)", "Service Definitions (create/update/delete)"))
console = Console()


def _wrap_definition(def_obj: dict, as_list: bool = True) -> dict:
    """
    Wrap a minimal definition into the JSON:API structure used by
    POST /api/v2/services/definitions.
    If def_obj already looks like {"data":[...]} it is returned as-is.
    """
    if isinstance(def_obj, dict) and "data" in def_obj:
        return def_obj
    item = {"type": "service_definition", "attributes": def_obj}
    if as_list:
        return {"data": [item]}
    return {"data": item}


def _build_minimal_definition(
    service: str,
    schema_version: str,
    description: Optional[str],
    env: Optional[str],
    team: Optional[str],
    application: Optional[str],
    tier: Optional[str],
    tags: List[str],
) -> dict:
    schema: dict = {
        "dd-service": service,
    }

    if tier:
        schema["tier"] = tier

    if team:
        schema["team"] = team

    if application:
        schema["application"] = application

    full_tags: List[str] = []
    if env:
        full_tags.append(f"env:{env}")
    if tags:
        full_tags.extend(tags)
    if full_tags:
        schema["tags"] = full_tags

    if description:
        schema.setdefault("description", description)

    return {
        "schema_version": schema_version,
        "schema": schema,
    }

def _render_definitions_table(items: list[dict]) -> None:
    """
    Render minimal columns:
    - dd-service (attributes.service)
    - team
    - tags (comma-separated)
    - origin-detail (best-effort: origin or origin_detail)
    """
    table = Table(title="Service Definitions", show_lines=False)
    table.add_column("dd-service", style="magenta", no_wrap=True)
    table.add_column("team", style="cyan", no_wrap=True)
    table.add_column("tags", style="white")
    table.add_column("origin-detail", style="green")

    for item in items:
        attrs = (item or {}).get("attributes") or {}
        # Newer API shapes place values under attributes.schema / attributes.meta
        schema = (attrs.get("schema") or {}) if isinstance(attrs, dict) else {}
        meta = (attrs.get("meta") or {}) if isinstance(attrs, dict) else {}
        if not attrs and isinstance(item, dict) and "service" in item:
            attrs = item  # attributes provided directly
        service = schema.get("dd-service") or attrs.get("service") or ""
        team = schema.get("team") or attrs.get("team") or ""
        tags = schema.get("tags") if "tags" in schema else attrs.get("tags")
        if tags is None:
            tags = []
        if isinstance(tags, list):
            tags_str = ", ".join(str(t) for t in tags)
        else:
            tags_str = str(tags)
        origin = (
            meta.get("origin-detail")
            or meta.get("origin")
            or attrs.get("origin_detail")
            or attrs.get("origin-detail")
            or ""
        )
        table.add_row(str(service), str(team), tags_str, str(origin))
    console.print(table)


@app.command(
    "apply",
    help=t(
        "Create/Update Service Definition from YAML file or flags",
        "Create/Update Service Definition from YAML file or flags",
    ),
)
def apply_definition(
    ctx: typer.Context,
    file: Optional[Path] = typer.Option(
        None, "--file", "-f", help=t("Path to YAML definition", "Path to YAML definition")
    ),
    service: Optional[str] = typer.Option(None, "--service", help="Service name"),
    schema_version: str = typer.Option("v2.1", "--schema-version", help="Schema version"),
    description: Optional[str] = typer.Option(None, "--description", help="Service description"),
    env: Optional[str] = typer.Option(None, "--env", help="Environment tag (adds env:<val>)"),
    team: Optional[str] = typer.Option(None, "--team", help="Owning team"),
    application: Optional[str] = typer.Option(None, "--application", help="Application name"),
    tier: Optional[str] = typer.Option(None, "--tier", help="Service tier"),
    tag: List[str] = typer.Option(None, "--tag", help="Extra tag key:value (repeatable)"),
    debug: bool = typer.Option(False, "--debug", help="Show request/response"),
) -> None:
    """
    Upserts service definitions using Datadog Service Definition API.
    - If --file is provided, YAML is loaded and sent as-is (wrapped if needed).
    - Else, minimal attributes are built from flags.
    """
    client = get_client_from_ctx(ctx)
    try:
        if file:
            data = yaml.safe_load(Path(file).read_text(encoding="utf-8"))
            payload = _wrap_definition(data)
        else:
            if not service:
                raise typer.BadParameter("--service is required when --file is not used")
            attrs = _build_minimal_definition(service, schema_version, description, env, team, application, tier, tag or [])
            payload = _wrap_definition(attrs)
        if debug:
            console.rule("service-definition payload")
            console.print(RichJSON.from_data(payload))
        resp = client.post("/api/v2/services/definitions", json=payload)
        console.print(RichJSON.from_data(resp))
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


@app.command(
    "get",
    help=t("Get a Service Definition by name", "Get a Service Definition by name"),
)
def get_definition(
    ctx: typer.Context,
    service: str = typer.Option(..., "--service", help="Service name"),
    debug: bool = typer.Option(False, "--debug", help="Show HTTP response"),
) -> None:
    """
    Calls GET /api/v2/services/definitions/{service_name}
    """
    client = get_client_from_ctx(ctx)
    try:
        resp = client.get(f"/api/v2/services/definitions/{service}") or {}
        if debug:
            console.print(RichJSON.from_data(resp))
            return
        data = resp.get("data")
        items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        _render_definitions_table(items)
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


@app.command(
    "list",
    help=t("List Service Definitions", "List Service Definitions"),
)
def list_definitions(
    ctx: typer.Context,
    debug: bool = typer.Option(False, "--debug", help="Show HTTP response"),
) -> None:
    """
    Calls GET /api/v2/services/definitions
    """
    client = get_client_from_ctx(ctx)
    try:
        resp = client.get("/api/v2/services/definitions") or {}
        if debug:
            console.print(RichJSON.from_data(resp))
            return
        items: list[dict] = []
        data = resp.get("data")
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # Some responses might nest under attributes.definitions
            attrs = data.get("attributes") or {}
            defs = attrs.get("definitions")
            if isinstance(defs, list):
                items = [{"attributes": d} for d in defs]
            else:
                items = [data]
        elif isinstance(resp, list):
            items = resp  # fallback

        _render_definitions_table(items)
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc

@app.command(
    "delete",
    help=t("Delete a Service Definition by name", "Delete a Service Definition by name"),
)
def delete_definition(
    ctx: typer.Context,
    service: str = typer.Option(..., "--service", help="Service name"),
    debug: bool = typer.Option(False, "--debug", help="Show response details"),
) -> None:
    """
    Calls DELETE /api/v2/services/definitions/{service_name}
    """
    client = get_client_from_ctx(ctx)
    try:
        resp = client.request("DELETE", f"/api/v2/services/definitions/{service}")
        if debug:
            console.print(RichJSON.from_data(resp if isinstance(resp, dict) else {"response": resp}))
        console.print(t("Service definition deleted (if it existed).", "Service definition deleted (if it existed)."))
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc

