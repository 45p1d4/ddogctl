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

app = typer.Typer(help=t("Service Catalog (Software Catalog v3)", "Service Catalog (Software Catalog v3)"))
console = Console()


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _build_entity_payload(
    service: str,
    description: Optional[str],
    env: Optional[str],
    team: Optional[str],
    tier: Optional[str],
    tags: List[str],
) -> dict:
    if tier:
        try:
            tier_value = str(int(tier))
        except ValueError:
            raise typer.BadParameter("--tier must be an integer (1-4)")
    else:
        tier_value = None

    all_tags: List[str] = []
    if env:
        all_tags.append(f"env:{env}")
    if team:
        all_tags.append(f"team:{team}")
    if tags:
        all_tags.extend(tags)

    payload = {
        "apiVersion": "v3",
        "kind": "service",
        "metadata": {
            "name": service,
            "displayName": service.replace("-", " ").upper(),
            "description": description or "",
            "tags": all_tags,
            "owner": team,
        },
        "spec": {}
    }

    if tier_value:
        payload["spec"]["tier"] = tier_value

    return payload


def _render_entities_table(items: list[dict]) -> None:
    table = Table(title="Service Catalog", show_lines=False)
    table.add_column("service", style="magenta", no_wrap=True)
    table.add_column("owner", style="cyan")
    table.add_column("tier", style="yellow")
    table.add_column("tags", style="white")

    for item in items:
        attrs = item.get("attributes", {})
        schema = item.get("included_schema", {})

        name = attrs.get("name", "")
        owner = attrs.get("owner", "")
        tags = ", ".join(attrs.get("tags", []))
        tier = schema.get("spec", {}).get("tier", "")

        table.add_row(name, owner, str(tier), tags)

    console.print(table)


# -------------------------------------------------------------------
# Commands
# -------------------------------------------------------------------

@app.command(
    "apply",
    help=t("Create or update a Service Catalog entity (v3)", "Create or update a Service Catalog entity (v3)"),
)
def apply_service(
    ctx: typer.Context,
    service: str = typer.Option(..., "--service", help="Service name"),
    description: Optional[str] = typer.Option(None, "--description", help="Service description"),
    env: Optional[str] = typer.Option(None, "--env", help="Environment tag"),
    team: Optional[str] = typer.Option(None, "--team", help="Owning team"),
    tier: Optional[str] = typer.Option(None, "--tier", help="Service tier (1-4)"),
    tag: List[str] = typer.Option(None, "--tag", help="Extra tag key:value"),
    debug: bool = typer.Option(False, "--debug", help="Show request/response"),
) -> None:
    client = get_client_from_ctx(ctx)

    payload = _build_entity_payload(
        service=service,
        description=description,
        env=env,
        team=team,
        tier=tier,
        tags=tag or [],
    )

    if debug:
        console.rule("software-catalog payload")
        console.print(RichJSON.from_data(payload))

    try:
        resp = client.post("/api/v2/catalog/entity", json=payload)
        console.print(RichJSON.from_data(resp))
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


@app.command(
    "get",
    help=t("Get a Service Catalog entity by name", "Get a Service Catalog entity by name"),
)
def get_service(
    ctx: typer.Context,
    service: str = typer.Option(..., "--service", help="Service name"),
    debug: bool = typer.Option(False, "--debug", help="Show HTTP response"),
) -> None:
    client = get_client_from_ctx(ctx)

    try:
        resp = client.get("/api/v2/catalog/entity", params={"filter[name]": service})
        if debug:
            console.print(RichJSON.from_data(resp))
            return

        items = resp.get("data", [])
        _render_entities_table(items)
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


@app.command(
    "list",
    help=t("List Service Catalog entities", "List Service Catalog entities"),
)
def list_services(
    ctx: typer.Context,
    debug: bool = typer.Option(False, "--debug", help="Show HTTP response"),
) -> None:
    client = get_client_from_ctx(ctx)

    try:
        resp = client.get("/api/v2/catalog/entity")
        if debug:
            console.print(RichJSON.from_data(resp))
            return

        items = resp.get("data", [])
        _render_entities_table(items)
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc
