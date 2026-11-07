from __future__ import annotations

import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ..cli import get_client_from_ctx
from ..utils_time import parse_time
from ..i18n import t
from ..api import ApiError
from ..ui import emit, new_table, fail

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

def _apply_rollup(q: str, rollup: Optional[int]) -> str:
    """
    GET /api/v1/query has no `rollup` parameter (it is silently ignored), so
    the rollup must be part of the query itself: `<metric>{...}.rollup(avg, N)`.
    Left untouched when the query already sets a rollup or is a formula with
    several metrics (where a trailing rollup would only bind to the last one).
    """
    if not rollup or ".rollup(" in q:
        return q
    import re as _re
    bare = _re.sub(r"\{[^}]*\}", "{}", q)  # tag values may contain '-' or ','
    if _re.search(r"[+*/,]|\s-\s", bare):
        sys.stderr.write(
            f"warning: --rollup ignored for formula query; add .rollup(avg,{rollup}) to each metric\n"
        )
        return q
    return f"{q}.rollup(avg,{int(rollup)})"


def _query_last_point(client, q: str, start_s: int, end_s: int, rollup: Optional[int], debug: bool) -> Optional[float]:
    return _query_stats(client, q, start_s, end_s, rollup, debug)["last"]


def _query_stats(client, q: str, start_s: int, end_s: int, rollup: Optional[int], debug: bool) -> dict:
    """{last, avg, max} of the first series of `q` over the window (None when no data)."""
    params = {"from": start_s, "to": end_s, "query": _apply_rollup(q, rollup)}
    with console.status(f"[dim]Consultando métricas[/dim] {q}"):
        resp = client.get("/api/v1/query", params=params) or {}
    if debug:
        try:
            from rich.json import JSON as RichJSON
            console.rule(q)
            console.print(RichJSON.from_data(resp))
        except Exception:
            pass
    series = resp.get("series") or []
    vals = _point_values(series[0].get("pointlist") or []) if series else []
    if not vals:
        return {"last": None, "avg": None, "max": None}
    return {"last": vals[-1], "avg": sum(vals) / len(vals), "max": max(vals)}


def _point_values(points: list) -> list[float]:
    """Numeric values of a pointlist, skipping null gaps (Datadog emits [ts, null])."""
    out: list[float] = []
    for p in points or []:
        if isinstance(p, (list, tuple)) and len(p) >= 2 and p[1] is not None:
            try:
                out.append(float(p[1]))
            except (TypeError, ValueError):
                continue
    return out


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
        params = {"from": start, "to": end, "query": _apply_rollup(query, rollup)}
        with console.status("[dim]Consultando series temporales[/dim]"):
            resp = client.get("/api/v1/query", params=params) or {}
        if debug:
            from rich.json import JSON as RichJSON
            console.print(RichJSON.from_data(resp))
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

        def _sparkline(points: list) -> str:
            vals = _point_values(points)
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

        def _last_point(points: list):
            """Last [ts_ms, value] whose value is not null, or None."""
            for p in reversed(points or []):
                if isinstance(p, (list, tuple)) and len(p) >= 2 and p[1] is not None:
                    try:
                        return p[0], float(p[1])
                    except (TypeError, ValueError):
                        continue
            return None

        def _fmt_ts(ts_ms) -> str:
            try:
                return _dt.fromtimestamp(float(ts_ms) / 1000.0, tz=_tz.utc).strftime("%H:%M:%S")
            except Exception:
                return str(ts_ms)

        # sort by last value (desc) and limit; series without data go last
        def _sort_key(s: dict) -> float:
            lp = _last_point(s.get("pointlist") or [])
            return lp[1] if lp else float("-inf")

        series_sorted = sorted(series, key=_sort_key, reverse=True)[:limit]

        from datetime import datetime as _dt, timezone as _tz

        table = new_table("Metrics (latest point per series)", {"from": from_, "to": to})
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

        # Build normalized rows for JSON contract + table rows from the same stats
        normalized_rows = []
        for s in series_sorted:
            metric = s.get("metric", "")
            scope = _extract_scope(s)
            points = s.get("pointlist") or []
            row: dict = {"metric": str(metric), "scope": scope, "n_points": len(points)}
            cells = [str(metric), scope, str(len(points)), "", "", "", "", ""]
            lp = _last_point(points)
            if lp:
                ts_ms, val = lp
                row["last_ts"] = _fmt_ts(ts_ms)
                row["last"] = round(val, 4)
                cells[3], cells[4] = row["last_ts"], _fmt_decimal(val)
            vs = _point_values(points)
            if vs:
                avg, mn, mx = sum(vs) / len(vs), min(vs), max(vs)
                row["avg"], row["min"], row["max"] = round(avg, 4), round(mn, 4), round(mx, 4)
                cells[5], cells[6], cells[7] = _fmt_decimal(avg), _fmt_decimal(mn), _fmt_decimal(mx)
            normalized_rows.append(row)
            if spark:
                cells.append(_sparkline(points))
            table.add_row(*cells)

        emit(
            ctx,
            "metrics.query",
            normalized_rows,
            raw=resp,
            meta={"from": from_, "to": to, "query": query},
            table_renderer=lambda: console.print(table),
        )
    except Exception as exc:
        fail(ctx, "metrics.query", exc, debug)

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
      - cpu: sum:kubernetes.cpu.requests / sum:kubernetes.cpu.limits / sum:kubernetes.cpu.usage.total (nanocores)
      - mem: sum:kubernetes.memory.requests / sum:kubernetes.memory.limits / sum:container.memory.usage
    y muestra requests/limits (último valor) y usage (promedio y máximo de la ventana).
    """
    if not kube_service and not kube_deployment:
        fail(ctx, "metrics.k8s", typer.BadParameter(t(
            "Debe especificar --kube-service o --kube-deployment",
            "Specify --kube-service or --kube-deployment",
        )), debug)
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
        # kubernetes.cpu.usage.total is a *gauge* in nanocores (cores in use),
        # not a cumulative counter, so no .as_rate(): it was a no-op on gauges
        # and misleading. Verified against live data: value/1e9 ~ cores.
        q_cpu_use = f"sum:kubernetes.cpu.usage.total{{{tag_filter}}}"

        q_mem_req = f"sum:kubernetes.memory.requests{{{tag_filter}}}"
        q_mem_lim = f"sum:kubernetes.memory.limits{{{tag_filter}}}"
        q_mem_use = f"sum:container.memory.usage{{{tag_filter}}}"

        cpu_req = _query_last_point(client, q_cpu_req, start, end, rollup, debug)
        cpu_lim = _query_last_point(client, q_cpu_lim, start, end, rollup, debug)
        # Usage is volatile minute to minute (e.g. 0.2 -> 1.9 -> 0.4 cores), so a
        # single "last point" is misleading: report avg and max over the window.
        cpu_use = _query_stats(client, q_cpu_use, start, end, rollup, debug)
        # kubernetes.cpu.usage.total viene en nanocores -> convertir a cores
        cpu_use = {k: (v / 1_000_000_000.0 if v is not None else None) for k, v in cpu_use.items()}

        mem_req = _query_last_point(client, q_mem_req, start, end, rollup, debug)
        mem_lim = _query_last_point(client, q_mem_lim, start, end, rollup, debug)
        mem_use = _query_stats(client, q_mem_use, start, end, rollup, debug)

        target = kube_deployment or kube_service or ""
        table = new_table("K8s resources", {"cluster": cluster, "service": target, "from": from_, "to": to})
        table.add_column("resource", style="magenta")
        table.add_column("requests", style="cyan", no_wrap=True)
        table.add_column("limits", style="cyan", no_wrap=True)
        table.add_column("usage avg", style="yellow", no_wrap=True)
        table.add_column("usage max", style="yellow", no_wrap=True)
        table.add_column("avg/req", style="white", no_wrap=True)

        def fmt(val, unit):
            if val is None:
                return "-"
            if unit == "bytes":
                return _fmt_bytes(val)
            # CPU
            if cpu_unit.lower() in ("mcores", "millicores", "mcore"):
                return f"{_fmt_decimal(val * 1000)} mCores"
            return f"{_fmt_decimal(val)} cores"

        def pct(use, req):
            if use is None or not req:
                return None
            return round(use / req * 100, 1)

        rows = []
        for name, unit, req, lim, use in (
            ("cpu", "cores", cpu_req, cpu_lim, cpu_use),
            ("memory", "bytes", mem_req, mem_lim, mem_use),
        ):
            row = {
                "resource": name, "unit": unit, "requests": req, "limits": lim,
                "usage_avg": use["avg"], "usage_max": use["max"], "usage_last": use["last"],
                "usage_pct_of_requests": pct(use["avg"], req),
            }
            rows.append(row)
            p_ = row["usage_pct_of_requests"]
            table.add_row(
                name, fmt(req, unit), fmt(lim, unit), fmt(use["avg"], unit), fmt(use["max"], unit),
                "-" if p_ is None else f"{p_}%",
            )

        # 3) Say so when nothing matched instead of a silent table of dashes.
        no_data = all(
            v is None for r in rows for v in (r["requests"], r["limits"], r["usage_avg"])
        )
        hint = None
        if no_data:
            tag = "kube_deployment" if kube_deployment else "kube_service"
            hint = t(
                f"Sin datos para cluster:{cluster} + {tag}:{target} en el rango. Revisa los nombres, p.ej.: "
                f"ddogctl metrics query --query 'sum:kubernetes.cpu.requests{{cluster:{cluster}}} by {{{tag}}}' --scope-tag {tag}",
                f"No data for cluster:{cluster} + {tag}:{target} in range. Check the names, e.g.: "
                f"ddogctl metrics query --query 'sum:kubernetes.cpu.requests{{cluster:{cluster}}} by {{{tag}}}' --scope-tag {tag}",
            )

        def _render() -> None:
            console.print(table)
            if hint:
                console.print(f"[yellow]{hint}[/yellow]")

        meta = {"cluster": cluster, "kube_service": kube_service, "kube_deployment": kube_deployment,
                "from": from_, "to": to, "rollup": rollup}
        if no_data:
            meta["no_data"] = True
            meta["hint"] = hint
        emit(ctx, "metrics.k8s", rows, meta=meta, table_renderer=_render)
    except Exception as exc:
        fail(ctx, "metrics.k8s", exc, debug)


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
        # Response shape can vary; attempt to read keys commonly returned
        data = resp.get("data") or resp
        metrics = data.get("metrics") if isinstance(data, dict) else None
        entries = metrics or []
        rows = []
        if isinstance(entries, list):
            for e in entries:
                rows.append({
                    "tag_key": e.get("tag_key") or e.get("name") or "",
                    "cardinality": e.get("cardinality") or e.get("count") or 0,
                })
        else:
            for key, val in (entries or {}).items():
                rows.append({
                    "tag_key": str(key),
                    "cardinality": (val.get("cardinality") if isinstance(val, dict) else val) or 0,
                })

        def _render() -> None:
            table = Table(title=f"Tag cardinality for {metric}", show_lines=False)
            table.add_column("tag_key", style="magenta")
            table.add_column("cardinality", style="yellow", no_wrap=True)
            for r in rows:
                table.add_row(str(r["tag_key"]), str(r["cardinality"]))
            console.print(table)

        emit(ctx, "metrics.tag_cardinality", rows, raw=resp, meta={"metric": metric}, table_renderer=_render)
    except Exception as exc:
        fail(ctx, "metrics.tag_cardinality", exc, debug)

