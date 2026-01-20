from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ..cli import get_client_from_ctx
from ..utils_time import parse_time
from ..i18n import t
from ..api import ApiError

app = typer.Typer(help=t("Operaciones de Métricas", "Metrics operations"))
console = Console()

def _fmt_decimal(val: float, decimals: int = 4) -> str:
    try:
        s = f"{float(val):.{decimals}f}"
        s = s.rstrip("0").rstrip(".")
        return s if s else "0"
    except Exception:
        return str(val)

def _fmt_bytes(val: float) -> str:
    try:
        units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
        x = float(val)
        i = 0
        while x >= 1024.0 and i < len(units) - 1:
            x /= 1024.0
            i += 1
        return f"{_fmt_decimal(x, 2)} {units[i]}"
    except Exception:
        return str(val)

def _query_last_point(client, q: str, start_s: int, end_s: int, rollup: Optional[int], debug: bool) -> Optional[float]:
    params = {"from": start_s, "to": end_s, "query": q}
    if rollup:
        params["rollup"] = rollup
    resp = client.get("/api/v1/query", params=params) or {}
    if debug:
        try:
            from rich.json import JSON as RichJSON
            console.rule(q)
            console.print(RichJSON.from_data(resp))
        except Exception:
            pass
    series = resp.get("series") or []
    if not series:
        return None
    points = series[0].get("pointlist") or []
    if not points:
        return None
    try:
        return float(points[-1][1])
    except Exception:
        return None


@app.command(
    "query",
    help=t(
        "Consulta series temporales (GET /api/v1/query) con una métrica/consulta",
        "Query timeseries (GET /api/v1/query) with a metric/query",
    ),
)
def metrics_query(
    ctx: typer.Context,
    query: str = typer.Option(..., "--query", help=t("Consulta, p.ej. avg:system.cpu.user{*}", "Query, e.g., avg:system.cpu.user{*}")),
    from_: str = typer.Option("now-1h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    rollup: Optional[int] = typer.Option(None, "--rollup", help=t("Agregación por segundos (opcional)", "Rollup seconds (optional)")),
    limit: int = typer.Option(20, "--limit", help=t("Máximo de series a mostrar", "Max series to display"), show_default=True),
    scope_tag: Optional[str] = typer.Option(None, "--scope-tag", help=t("Mostrar solo este tag del scope (p.ej. kube_deployment)", "Show only this tag from scope (e.g., kube_deployment)")),
    spark: bool = typer.Option(False, "--spark", help=t("Mostrar sparkline (mini-gráfico) por serie", "Show sparkline per series")),
    spark_points: int = typer.Option(30, "--spark-points", help=t("Cantidad de puntos para sparkline", "Points for sparkline"), show_default=True),
    debug: bool = typer.Option(False, "--debug", help="Show raw response/errors"),
) -> None:
    """
    Calls GET /api/v1/query?from=<epoch_s>&to=<epoch_s>&query=<query>[&rollup]
    Renders a compact table with the latest value per series.
    """
    client = get_client_from_ctx(ctx)
    try:
        start = int(parse_time(from_).timestamp())
        end = int(parse_time(to).timestamp())
        params = {"from": start, "to": end, "query": query}
        if rollup:
            params["rollup"] = rollup
        resp = client.get("/api/v1/query", params=params) or {}
        if debug:
            from rich.json import JSON as RichJSON
            console.print(RichJSON.from_data(resp))
            return
        series = resp.get("series") or []

        def _extract_scope(s: dict) -> str:
            scope = s.get("scope") or ""
            if not scope_tag:
                return scope
            for part in scope.split(","):
                part = part.strip()
                if part.startswith(f"{scope_tag}:"):
                    return part
            return scope

        def _sparkline(points: list[list]) -> str:
            if not points:
                return ""
            vals = [p[1] for p in points if p and isinstance(p, list)]
            if not vals:
                return ""
            if len(vals) > spark_points:
                # sample uniformly
                step = max(1, len(vals) // spark_points)
                vals = vals[-spark_points * step :: step]
            mn, mx = min(vals), max(vals)
            blocks = "▁▂▃▄▅▆▇█"
            if mx == mn:
                return blocks[0] * len(vals)
            res = []
            for v in vals:
                idx = int((v - mn) / (mx - mn) * (len(blocks) - 1))
                res.append(blocks[idx])
            return "".join(res)

        # sort by last value (desc) and limit
        def _last_value(s: dict) -> float:
            pl = s.get("pointlist") or []
            if not pl:
                return float("nan")
            try:
                return float(pl[-1][1])
            except Exception:
                return float("nan")

        series_sorted = sorted(series, key=_last_value, reverse=True)[:limit]

        table = Table(title="Metrics (latest point per series)", show_lines=False)
        table.add_column("metric", style="magenta")
        table.add_column(scope_tag or "scope", style="cyan")
        table.add_column("pts", style="white", no_wrap=True)
        table.add_column("last_ts", style="white", no_wrap=True)
        table.add_column("last", style="yellow", no_wrap=True)
        table.add_column("avg", style="white", no_wrap=True)
        table.add_column("min", style="white", no_wrap=True)
        table.add_column("max", style="white", no_wrap=True)
        if spark:
            table.add_column("spark", style="green")

        from datetime import datetime, timezone

        for s in series_sorted:
            metric = s.get("metric", "")
            scope = _extract_scope(s)
            points = s.get("pointlist") or []
            last_ts = ""
            last_val = ""
            avg_val = ""
            min_val = ""
            max_val = ""
            if points:
                # pointlist: [[ts_ms, value], ...]
                ts_ms, val = points[-1]
                try:
                    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
                    last_ts = dt.strftime("%H:%M:%S")
                except Exception:
                    last_ts = str(int(ts_ms / 1000))
                last_val = _fmt_decimal(val)
                vs = [p[1] for p in points if p and isinstance(p, list)]
                if vs:
                    avg_val = _fmt_decimal(sum(vs)/len(vs))
                    min_val = _fmt_decimal(min(vs))
                    max_val = _fmt_decimal(max(vs))
            row = [str(metric), scope, str(len(points)), last_ts, last_val, avg_val, min_val, max_val]
            if spark:
                row.append(_sparkline(points))
            table.add_row(*row)
        console.print(table)
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc

@app.command(
    "k8s-resources",
    help=t(
        "Resumen CPU/Mem (requests/limits/usage) por servicio/deployment",
        "CPU/Memory summary (requests/limits/usage) per service/deployment",
    ),
)
def k8s_resources(
    ctx: typer.Context,
    cluster: str = typer.Option(..., "--cluster", help="cluster:<name> tag value"),
    kube_service: Optional[str] = typer.Option(None, "--kube-service", help="kube_service tag value"),
    kube_deployment: Optional[str] = typer.Option(None, "--kube-deployment", help="kube_deployment tag value"),
    from_: str = typer.Option("now-1h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    rollup: Optional[int] = typer.Option(60, "--rollup", help=t("Agregación por segundos", "Rollup seconds"), show_default=True),
    cpu_unit: str = typer.Option("cores", "--cpu-unit", help=t("Unidad de CPU (cores|mcores)", "CPU unit (cores|mcores)"), show_default=True),
    debug: bool = typer.Option(False, "--debug", help="Show raw queries/responses"),
) -> None:
    """
    Construye y ejecuta consultas para:
      - cpu: sum:kubernetes.cpu.requests / sum:kubernetes.cpu.limits / sum:kubernetes.cpu.usage.total.as_rate()
      - mem: sum:kubernetes.memory.requests / sum:kubernetes.memory.limits / sum:container.memory.usage
    y muestra una tabla compacta con los últimos valores.
    """
    if not kube_service and not kube_deployment:
        raise typer.BadParameter("Debe especificar --kube-service o --kube-deployment")
    tag_filter = f"cluster:{cluster}"
    if kube_service:
        tag_filter += f",kube_service:{kube_service}"
    if kube_deployment:
        tag_filter += f",kube_deployment:{kube_deployment}"

    client = get_client_from_ctx(ctx)
    try:
        start = int(parse_time(from_).timestamp())
        end = int(parse_time(to).timestamp())

        q_cpu_req = f"sum:kubernetes.cpu.requests{{{tag_filter}}}"
        q_cpu_lim = f"sum:kubernetes.cpu.limits{{{tag_filter}}}"
        # Uso de CPU como tasa (cores por segundo) desde el contador acumulativo; sumar todas las series
        q_cpu_use = f"sum:kubernetes.cpu.usage.total{{{tag_filter}}}.as_rate()"

        q_mem_req = f"sum:kubernetes.memory.requests{{{tag_filter}}}"
        q_mem_lim = f"sum:kubernetes.memory.limits{{{tag_filter}}}"
        q_mem_use = f"sum:container.memory.usage{{{tag_filter}}}"

        cpu_req = _query_last_point(client, q_cpu_req, start, end, rollup, debug)
        cpu_lim = _query_last_point(client, q_cpu_lim, start, end, rollup, debug)
        cpu_use = _query_last_point(client, q_cpu_use, start, end, rollup, debug)
        # kubernetes.cpu.usage.total.as_rate() devuelve nanocores/seg -> convertir a cores
        if cpu_use is not None:
            try:
                cpu_use = float(cpu_use) / 1_000_000_000.0
            except Exception:
                pass

        mem_req = _query_last_point(client, q_mem_req, start, end, rollup, debug)
        mem_lim = _query_last_point(client, q_mem_lim, start, end, rollup, debug)
        mem_use = _query_last_point(client, q_mem_use, start, end, rollup, debug)

        table = Table(title=f"K8s resources ({kube_service or kube_deployment} @ {cluster})", show_lines=False)
        table.add_column("resource", style="magenta")
        table.add_column("requests", style="cyan", no_wrap=True)
        table.add_column("limits", style="cyan", no_wrap=True)
        table.add_column("usage", style="yellow", no_wrap=True)

        def fmt(val, unit):
            if val is None:
                return "-"
            if unit == "bytes":
                return _fmt_bytes(val)
            # CPU
            if cpu_unit.lower() in ("mcores", "millicores", "mcore"):
                return f"{_fmt_decimal(val * 1000)} mCores"
            return f"{_fmt_decimal(val)} cores"

        table.add_row("cpu", fmt(cpu_req, "cores"), fmt(cpu_lim, "cores"), fmt(cpu_use, "cores"))
        table.add_row("memory", fmt(mem_req, "bytes"), fmt(mem_lim, "bytes"), fmt(mem_use, "bytes"))
        console.print(table)
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


@app.command(
    "tag-cardinality",
    help=t(
        "Cardinalidad por tag (GET /api/v2/metrics/{metric}/tag-cardinality-details)",
        "Tag cardinality (GET /api/v2/metrics/{metric}/tag-cardinality-details)",
    ),
)
def metrics_tag_cardinality(
    ctx: typer.Context,
    metric: str = typer.Option(..., "--metric", help=t("Nombre de la métrica", "Metric name")),
    debug: bool = typer.Option(False, "--debug", help="Show raw response/errors"),
) -> None:
    """
    Calls GET /api/v2/metrics/{metric_name}/tag-cardinality-details
    Renders a table with tag_key and cardinality (when available).
    """
    client = get_client_from_ctx(ctx)
    try:
        resp = client.get(f"/api/v2/metrics/{metric}/tag-cardinality-details") or {}
        if debug:
            from rich.json import JSON as RichJSON
            console.print(RichJSON.from_data(resp))
            return
        # Response shape can vary; attempt to read keys commonly returned
        data = resp.get("data") or resp
        metrics = data.get("metrics") if isinstance(data, dict) else None
        entries = metrics or []
        table = Table(title=f"Tag cardinality for {metric}", show_lines=False)
        table.add_column("tag_key", style="magenta")
        table.add_column("cardinality", style="yellow", no_wrap=True)
        if isinstance(entries, list):
            for e in entries:
                key = e.get("tag_key") or e.get("name") or ""
                card = e.get("cardinality") or e.get("count") or ""
                table.add_row(str(key), str(card))
        else:
            # Fallback if a dict keyed by tag
            for key, val in (entries or {}).items():
                card = val.get("cardinality") if isinstance(val, dict) else val
                table.add_row(str(key), str(card))
        console.print(table)
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc

