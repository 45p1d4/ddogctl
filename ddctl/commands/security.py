"""
`ddogctl security`: infosec analysis over APM traces (read-only).

- `security trace-scan`  scan every span of one trace for exposure/attack/transport issues
- `security discover`    scan a time window: attack signatures, auth-failure sources,
                         path enumeration and distributed (CIDR-concentrated) campaigns
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console

from ..api import ApiError
from ..cli import get_client_from_ctx
from ..i18n import t
from ..options import DebugOption
from ..security_rules import (
    SEVERITY_ORDER,
    SIGNATURE_QUERIES,
    cdn_of,
    cidr_concentration,
    client_ip_from_xff,
    group_findings,
    scan_spans,
    severity_summary,
    sort_findings,
)
from ..normalize import extract_buckets, bucket_count
from ..ui import emit, fail, is_json, new_table
from ..utils_time import parse_time, to_iso8601

app = typer.Typer(help=t(
    "Análisis de seguridad (infosec) sobre traces/spans — solo lectura",
    "Security (infosec) analysis over traces/spans — read-only",
))
console = Console()

SEV_STYLE = {"high": "bold red", "medium": "yellow", "low": "cyan", "info": "dim"}


def _window(from_: str, to: str):
    dt_from, dt_to = parse_time(from_), parse_time(to)
    if dt_to < dt_from:
        raise typer.BadParameter(t("--to debe ser >= --from", "--to must be >= --from"))
    return to_iso8601(dt_from), to_iso8601(dt_to)


def _search_spans(client, query: str, f: str, to: str, limit: int, sort: str = "-timestamp") -> List[dict]:
    """POST /spans/events/search with cursor pagination up to `limit` spans."""
    items: List[dict] = []
    cursor: Optional[str] = None
    while len(items) < limit:
        page: Dict[str, Any] = {"limit": min(1000, limit - len(items))}
        if cursor:
            page["cursor"] = cursor
        body = {"data": {"type": "search_request", "attributes": {
            "filter": {"from": f, "to": to, "query": query}, "page": page, "sort": sort}}}
        resp = client.post("/api/v2/spans/events/search", json=body) or {}
        batch = resp.get("data") or []
        items.extend(batch)
        cursor = ((resp.get("meta") or {}).get("page") or {}).get("after")
        if not batch or not cursor:
            break
    return items[:limit]


def _aggregate(client, query: str, f: str, to: str, compute: List[dict],
               group_by: Optional[List[dict]] = None) -> dict:
    attrs: Dict[str, Any] = {"filter": {"from": f, "to": to, "query": query}, "compute": compute}
    if group_by:
        attrs["group_by"] = group_by
    return client.post("/api/v2/spans/analytics/aggregate",
                       json={"data": {"type": "aggregate_request", "attributes": attrs}}) or {}


def _combine(base: Optional[str], extra: str) -> str:
    base = (base or "").strip()
    if not base or base == "*":
        return f"({extra})"
    return f"({base}) ({extra})"


XFF_FACET = "@http.request.headers.x-forwarded-for"
CLIENT_IP_FACET = "@http.client_ip"


def _count(client, query: str, f: str, to: str) -> int:
    resp = _aggregate(client, query, f, to, [{"aggregation": "count"}])
    b = extract_buckets(resp)
    return bucket_count(b[0]) if b else 0


def _resolve_ip_source(client, base: Optional[str], f: str, to: str, ip_source: str) -> str:
    if ip_source != "auto":
        return ip_source
    # Prefer X-Forwarded-For when present: behind a CDN, @http.client_ip is the edge node.
    return "xff" if _count(client, _combine(base, f"{XFF_FACET}:*"), f, to) > 0 else "client_ip"


def _per_ip(client, query: str, f: str, to: str, ip_source: str, compute: List[dict],
            limit: int = 2000) -> Dict[str, Any]:
    """
    {ip: {"c0": count, "c1": ...}} keyed by the real client IP.
    - client_ip: group by @http.client_ip; CDN edge IPs are dropped (counted in cdn_edge_ips).
    - xff: group by the X-Forwarded-For chain and map each chain to its left-most
      public, non-CDN IP. Counts are summed; other computes keep the max
      (cardinality is not additive, so this is a lower bound).
    """
    facet = XFF_FACET if ip_source == "xff" else CLIENT_IP_FACET
    resp = _aggregate(client, _combine(query, f"{facet}:*"), f, to, compute,
                      [{"facet": facet, "limit": limit,
                        "sort": {"type": "measure", "aggregation": "count", "order": "desc"}}])
    out: Dict[str, Dict[str, float]] = {}
    cdn_hits = 0
    buckets = extract_buckets(resp)
    for b in buckets:
        ref = b.get("attributes") or b
        key = (ref.get("by") or {}).get(facet)
        comp = ref.get("compute") or {}
        if ip_source == "xff":
            ip = client_ip_from_xff(key)
        else:
            ip = str(key or "")
            if ip and cdn_of(ip):
                cdn_hits += 1
                ip = None
        if not ip:
            continue
        slot = out.setdefault(ip, {})
        for ck, cv in comp.items():
            try:
                val = float(cv or 0)
            except (TypeError, ValueError):
                continue
            slot[ck] = slot.get(ck, 0.0) + val if ck == "c0" else max(slot.get(ck, 0.0), val)
    return {"ips": out, "cdn_edge_ips": cdn_hits, "groups": len(buckets), "truncated": len(buckets) >= limit}


def _render_findings(title: str, findings: List[dict], meta: Dict[str, Any]) -> None:
    summary = meta.get("severity") or {}
    head = "  ".join(f"[{SEV_STYLE[s]}]{s}={summary.get(s, 0)}[/{SEV_STYLE[s]}]" for s in SEVERITY_ORDER)
    tbl = new_table(title, {k: meta.get(k) for k in ("service", "from", "to") if meta.get(k)})
    tbl.add_column("sev", no_wrap=True)
    tbl.add_column("n", justify="right", no_wrap=True)
    tbl.add_column("rule", style="magenta", no_wrap=True)
    tbl.add_column("service", style="cyan", no_wrap=True)
    tbl.add_column("resource", style="white")
    tbl.add_column("evidence (redacted)", style="white")
    for f_ in findings:
        sev = f_.get("severity", "info")
        tbl.add_row(f"[{SEV_STYLE.get(sev, '')}]{sev}[/{SEV_STYLE.get(sev, '')}]",
                    str(f_.get("occurrences") or 1), f_.get("rule", ""),
                    str(f_.get("service") or ""), str(f_.get("resource") or "")[:60],
                    str(f_.get("evidence") or f_.get("title") or "")[:110])
    console.print(tbl)
    console.print(head)
    if not findings:
        console.print("[green]No findings[/green]")


# --------------------------------------------------------------------------
# trace-scan
# --------------------------------------------------------------------------

@app.command(
    "trace-scan",
    help=t(
        "Analiza todos los spans de un trace: secretos/PII expuestos, payloads de ataque, HTTP sin TLS",
        "Scan every span of a trace: exposed secrets/PII, attack payloads, cleartext HTTP",
    ),
)
def trace_scan(
    ctx: typer.Context,
    trace_id: str = typer.Option(..., "--trace-id", help=t("ID del trace", "Trace ID")),
    from_: str = typer.Option("now-24h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    limit: int = typer.Option(1000, "--limit", help=t("Máximo de spans", "Max spans"), show_default=True),
    min_severity: str = typer.Option("info", "--min-severity", help="high|medium|low|info", show_default=True),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        if min_severity not in SEVERITY_ORDER:
            raise typer.BadParameter(f"--min-severity must be one of {list(SEVERITY_ORDER)}")
        f, to_iso = _window(from_, to)
        if not is_json(ctx):
            with console.status("[dim]Cargando spans del trace[/dim]"):
                items = _search_spans(client, f"trace_id:{trace_id}", f, to_iso, limit, sort="timestamp")
        else:
            items = _search_spans(client, f"trace_id:{trace_id}", f, to_iso, limit, sort="timestamp")
        findings = group_findings([x for x in scan_spans(items)
                                   if SEVERITY_ORDER[x["severity"]] <= SEVERITY_ORDER[min_severity]])
        services = sorted({(i.get("attributes") or {}).get("service") or "" for i in items} - {""})
        meta = {
            "trace_id": trace_id, "spans_scanned": len(items), "services": services,
            "severity": severity_summary(findings), "from": from_, "to": to,
        }
        if not items:
            meta["hint"] = t(
                "No se encontraron spans: amplía --from (los spans indexados tienen retención limitada)",
                "No spans found: widen --from (indexed spans have limited retention)",
            )

        def _render() -> None:
            console.print(f"[dim]trace {trace_id}: {len(items)} spans, services: {', '.join(services) or '-'}[/dim]")
            _render_findings("Trace security scan", findings, meta)
            if meta.get("hint"):
                console.print(f"[yellow]{meta['hint']}[/yellow]")

        emit(ctx, "security.trace_scan", findings, meta=meta, table_renderer=_render)
    except Exception as exc:
        fail(ctx, "security.trace_scan", exc, debug)


# --------------------------------------------------------------------------
# discover
# --------------------------------------------------------------------------

CHECKS = ("signatures", "auth", "enumeration", "cidr")


@app.command(
    "discover",
    help=t(
        "Escanea una ventana de tiempo: firmas de ataque, fuentes de fallos de auth, "
        "enumeración de rutas y campañas distribuidas por rango de IP",
        "Scan a time window: attack signatures, auth-failure sources, path enumeration "
        "and IP-range (CIDR) distributed campaigns",
    ),
)
def discover(
    ctx: typer.Context,
    query: Optional[str] = typer.Option(
        None, "--query",
        help=t("Filtro base de spans (p.ej. 'service:web env:prd')", "Base spans filter (e.g. 'service:web env:prd')"),
    ),
    from_: str = typer.Option("now-1h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    checks: str = typer.Option(
        ",".join(CHECKS), "--checks",
        help=t("Chequeos coma-separados", "Comma-separated checks") + f" ({', '.join(CHECKS)})",
        show_default=True,
    ),
    sample: int = typer.Option(
        25, "--sample",
        help=t("Spans de muestra por firma para confirmar y dar evidencia",
               "Sample spans per signature, to confirm and give evidence"),
        show_default=True,
    ),
    auth_threshold: int = typer.Option(
        100, "--auth-threshold",
        help=t("Mín. de 401/403 por IP para reportarla", "Min 401/403 per client IP to report it"),
        show_default=True,
    ),
    enum_threshold: int = typer.Option(
        30, "--enum-threshold",
        help=t("Mín. de rutas distintas con 404 por IP", "Min distinct 404 paths per client IP"),
        show_default=True,
    ),
    ip_source: str = typer.Option(
        "auto", "--ip-source",
        help=t("Origen de la IP del cliente: auto|xff|client_ip (auto usa X-Forwarded-For si existe)",
               "Client IP source: auto|xff|client_ip (auto uses X-Forwarded-For when present)"),
        show_default=True,
    ),
    cidr_min_ips: int = typer.Option(
        20, "--cidr-min-ips",
        help=t("Mín. de IPs distintas en un /16 para reportarlo", "Min distinct IPs within a /16 to report it"),
        show_default=True,
    ),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        wanted = [c.strip() for c in checks.split(",") if c.strip()]
        unknown = [c for c in wanted if c not in CHECKS]
        if unknown:
            raise typer.BadParameter(f"unknown --checks {unknown}; valid: {list(CHECKS)}")
        f, to_iso = _window(from_, to)
        findings: List[dict] = []
        signatures: List[dict] = []
        warnings: List[str] = []

        def _progress(msg: str) -> None:
            if not is_json(ctx):
                sys.stderr.write(f"· {msg}\n")
                sys.stderr.flush()

        # 1) Attack/exposure signatures: server-side count, sample-confirmed
        if "signatures" in wanted:
            for rule, sig_q in SIGNATURE_QUERIES:
                q = _combine(query, sig_q)
                _progress(f"signature {rule}")
                try:
                    resp = _aggregate(client, q, f, to_iso, [{"aggregation": "count"}],
                                      [{"facet": "service", "limit": 10,
                                        "sort": {"type": "measure", "aggregation": "count", "order": "desc"}}])
                except ApiError as e:
                    warnings.append(f"{rule}: HTTP {e.status_code}")
                    continue
                by_service = {}
                for b in extract_buckets(resp):
                    ref = b.get("attributes") or b
                    by_service[str((ref.get("by") or {}).get("service", ""))] = bucket_count(b)
                total = sum(by_service.values())
                entry = {"rule": rule, "candidates": total, "by_service": by_service, "confirmed": 0}
                if total and sample > 0:
                    items = _search_spans(client, q, f, to_iso, sample)
                    confirmed = [x for x in scan_spans(items) if x["rule"] == rule
                                 or (rule.startswith("injection_") and x["rule"].startswith("injection_"))]
                    entry["confirmed"] = len({x["span_id"] for x in confirmed})
                    entry["sampled"] = len(items)
                    findings.extend(confirmed)
                signatures.append(entry)

        need_ip = any(c in wanted for c in ("auth", "enumeration", "cidr"))
        if ip_source not in ("auto", "xff", "client_ip"):
            raise typer.BadParameter("--ip-source must be auto, xff or client_ip")
        src = _resolve_ip_source(client, query, f, to_iso, ip_source) if need_ip else ip_source
        cdn_edge_total = 0

        def _ip_finding(rule: str, severity: str, title: str, ip: str, evidence: str, **extra: Any) -> dict:
            return {"rule": rule, "severity": severity, "category": "attack", "title": title,
                    "service": "", "resource": "", "field": XFF_FACET if src == "xff" else CLIENT_IP_FACET,
                    "evidence": evidence, "client_ip": ip, **extra}

        # 2) Auth failures by client IP (brute force / credential stuffing)
        if "auth" in wanted:
            _progress(f"auth failures by client IP ({src})")
            try:
                r = _per_ip(client, _combine(query, "@http.status_code:(401 OR 403)"), f, to_iso, src,
                            [{"aggregation": "count"}], limit=500)
                cdn_edge_total += r["cdn_edge_ips"]
                for ip, c in sorted(r["ips"].items(), key=lambda kv: -kv[1].get("c0", 0))[:25]:
                    cnt = int(c.get("c0", 0))
                    if cnt >= auth_threshold:
                        findings.append(_ip_finding(
                            "auth_failure_burst", "high" if cnt >= auth_threshold * 10 else "medium",
                            "Many 401/403 responses for one client IP (brute force / credential stuffing?)",
                            ip, f"{ip}: {cnt} auth failures", count=cnt))
            except ApiError as e:
                warnings.append(f"auth: HTTP {e.status_code}")

        # 3) Path enumeration: many distinct 404 paths from one IP
        if "enumeration" in wanted:
            _progress(f"404 path enumeration by client IP ({src})")
            try:
                r = _per_ip(client, _combine(query, "@http.status_code:404"), f, to_iso, src,
                            [{"aggregation": "count"},
                             {"aggregation": "cardinality", "metric": "@http.url_details.path"}], limit=500)
                cdn_edge_total += r["cdn_edge_ips"]
                for ip, c in sorted(r["ips"].items(), key=lambda kv: -kv[1].get("c1", 0))[:25]:
                    paths, cnt = int(c.get("c1", 0)), int(c.get("c0", 0))
                    if paths >= enum_threshold:
                        findings.append(_ip_finding(
                            "path_enumeration", "high" if paths >= enum_threshold * 5 else "medium",
                            "One client IP hitting many distinct non-existent paths (content discovery scan)",
                            ip, f"{ip}: >= {paths} distinct 404 paths / {cnt} requests",
                            count=cnt, distinct_paths=paths))
            except ApiError as e:
                warnings.append(f"enumeration: HTTP {e.status_code}")

        # 4) Distributed campaign: many IPs from the same /16, low rate per IP
        cidrs: List[dict] = []
        if "cidr" in wanted:
            _progress(f"client IP concentration by /16 ({src})")
            try:
                r = _per_ip(client, query or "*", f, to_iso, src, [{"aggregation": "count"}], limit=5000)
                cdn_edge_total += r["cdn_edge_ips"]
                ip_counts = {ip: int(c.get("c0", 0)) for ip, c in r["ips"].items()}
                cidrs = cidr_concentration(ip_counts, cidr_min_ips)
                for g in cidrs[:10]:
                    findings.append(_ip_finding(
                        "distributed_ip_range", "medium" if g["share_pct"] >= 5 else "low",
                        "Traffic concentrated in one IP range from many rotating IPs (distributed scraping/scan?)",
                        g["prefix"],
                        (f"{g['prefix']}: {g['ips']} IPs, {g['requests']} req "
                         f"({g['share_pct']}% of analysed traffic), avg {g['avg_per_ip']}/IP"),
                        count=g["requests"]))
                if r["truncated"]:
                    warnings.append("cidr: top-5000 IP groups analysed; long tail not included")
            except ApiError as e:
                warnings.append(f"cidr: HTTP {e.status_code}")

        if cdn_edge_total:
            warnings.append(
                f"{cdn_edge_total} top client IPs are CDN edge nodes and were ignored; "
                "per-IP results need the real client IP (try --ip-source xff, or capture True-Client-IP)"
            )

        findings = group_findings(findings)
        meta = {
            "query": query or "*", "from": from_, "to": to, "checks": wanted, "ip_source": src,
            "severity": severity_summary(findings), "signatures": signatures,
        }
        if cidrs:
            meta["ip_ranges"] = cidrs[:10]
        if warnings:
            meta["warnings"] = warnings

        def _render() -> None:
            if signatures:
                tbl = new_table("Signatures (server-side candidates → confirmed in sample)")
                tbl.add_column("rule", style="magenta", no_wrap=True)
                tbl.add_column("candidates", justify="right")
                tbl.add_column("confirmed", justify="right")
                tbl.add_column("top services", style="cyan")
                for s in signatures:
                    top = ", ".join(f"{k}({v})" for k, v in list(s["by_service"].items())[:3])
                    conf = f"{s['confirmed']}/{s.get('sampled', 0)}" if s["candidates"] else "-"
                    tbl.add_row(s["rule"], str(s["candidates"]), conf, top)
                console.print(tbl)
            _render_findings("Security discover", findings, meta)
            for w in warnings:
                console.print(f"[yellow]warning: {w}[/yellow]")

        emit(ctx, "security.discover", findings, meta=meta, table_renderer=_render)
    except Exception as exc:
        fail(ctx, "security.discover", exc, debug)


# endpoint-scan lives in its own module; importing it registers the command on `app`.
from . import security_endpoint  # noqa: E402,F401
