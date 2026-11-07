"""
`ddogctl security endpoint-scan`: who is hitting an endpoint, and is it a scan?

Read-only. Combines server-side span aggregates (exact counts over the whole
window) with a span sample (evidence for header-level checks):

1. overview      requests, status codes, distinct client IPs
2. clients/bots  every User-Agent classified (browser, crawler, AI crawler,
                 HTTP library, headless, scanner, empty...) with share
3. spoofing      crawlers whose source AS cannot be theirs (fake Googlebot...)
4. network       origin AS types (hosting/datacenter vs ISP), top hosting ASNs
5. top IPs       real client IPs (behind CDN: X-Forwarded-For), rate, 4xx ratio,
                 distinct paths
6. botnets       same automated UA from many IPs; IP-range (/16) concentration
7. headers       attack payloads / spoofing in captured request headers
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

import typer

from ..api import ApiError
from ..cli import get_client_from_ctx
from ..i18n import t
from ..normalize import bucket_count, extract_buckets
from ..options import DebugOption
from ..security_rules import (
    AUTOMATED_CLASSES,
    CDN_AS_RE,
    cidr_concentration,
    classify_user_agent,
    dd_escape,
    group_findings,
    header_findings,
    is_spoofed_crawler,
    scan_spans,
    severity_summary,
    span_view,
    wildcard,
)
from ..ui import emit, fail, is_json, new_table
from .security import (
    CLIENT_IP_FACET,
    XFF_FACET,
    _aggregate,
    _combine,
    _per_ip,
    _render_findings,
    _resolve_ip_source,
    _search_spans,
    _window,
    app,
    console,
)

PRIVATE_CLIENT_IP_EXCLUDES = " ".join(
    [f"-{CLIENT_IP_FACET}:{p}" for p in ("10.*", "192.168.*", "127.*", "169.254.*")]
    + [f"-{CLIENT_IP_FACET}:172.{n}.*" for n in range(16, 32)]
)
UA_FACET = "@http.useragent"
AS_NAME = "@http.client_ip_details.as.name"
AS_TYPE = "@http.client_ip_details.as.type"


def _groups(resp: dict, facets: List[str]) -> List[Dict[str, Any]]:
    out = []
    for b in extract_buckets(resp):
        ref = b.get("attributes") or b
        by = ref.get("by") or {}
        comp = ref.get("compute") or {}
        row = {f: by.get(f) for f in facets}
        row["count"] = bucket_count(b)
        for k, v in comp.items():
            row[k] = v
        out.append(row)
    return out


def _sort(limit: int) -> Dict[str, Any]:
    return {"limit": limit, "sort": {"type": "measure", "aggregation": "count", "order": "desc"}}


def _build_query(service: Optional[str], path: Optional[str], resource: Optional[str],
                 extra: Optional[str], external_only: bool) -> str:
    terms: List[str] = []
    if service:
        terms.append(f"service:{service}")
    if path:
        # keep user wildcards (*), escape everything else
        literal = "*".join(dd_escape(p) for p in path.split("*"))
        terms.append(f"@http.url_details.path:{literal}")
    if resource:
        terms.append(f'resource_name:"{resource}"')
    if extra:
        terms.append(f"({extra})")
    terms.append("@http.url_details.path:*")  # inbound HTTP requests only
    if external_only:
        # External = a client IP was recorded and it is not private. Spans with no
        # client IP at all are usually internal hops/self-calls, not users.
        terms.append(f"({CLIENT_IP_FACET}:* OR {XFF_FACET}:*)")
        terms.append(PRIVATE_CLIENT_IP_EXCLUDES)
    return " ".join(terms)


@app.command(
    "endpoint-scan",
    help=t(
        "Detecta escaneos/bots/botnets contra un endpoint: user agents, headers maliciosos, "
        "IPs y ASN de origen, crawlers falsos, rangos de IP",
        "Detect scans/bots/botnets against an endpoint: user agents, malicious headers, "
        "source IPs and ASNs, fake crawlers, IP ranges",
    ),
)
def endpoint_scan(
    ctx: typer.Context,
    service: Optional[str] = typer.Option(None, "--service", help=t("Servicio", "Service")),
    path: Optional[str] = typer.Option(
        None, "--path", help=t("Ruta HTTP, admite * (p.ej. '/api/reviews*')", "HTTP path, * allowed (e.g. '/api/reviews*')")),
    resource: Optional[str] = typer.Option(None, "--resource", help=t("resource_name exacto", "Exact resource_name")),
    query: Optional[str] = typer.Option(None, "--query", help=t("Filtro adicional de spans", "Extra spans filter")),
    from_: str = typer.Option("now-1h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    top: int = typer.Option(10, "--top", help=t("Filas por sección", "Rows per section"), show_default=True),
    sample: int = typer.Option(
        300, "--sample", help=t("Spans de muestra para chequeos de headers", "Sample spans for header checks"),
        show_default=True),
    ip_source: str = typer.Option("auto", "--ip-source", help="auto|xff|client_ip", show_default=True),
    botnet_min_ips: int = typer.Option(
        20, "--botnet-min-ips",
        help=t("Mín. de IPs distintas con el mismo UA automatizado", "Min distinct IPs sharing one automated UA"),
        show_default=True),
    include_internal: bool = typer.Option(
        False, "--include-internal",
        help=t("Incluir tráfico desde IPs privadas (service-to-service)", "Include traffic from private IPs (service-to-service)")),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        if not (service or path or resource or query):
            raise typer.BadParameter(t(
                "Indica al menos --service, --path, --resource o --query",
                "Give at least --service, --path, --resource or --query",
            ))
        if ip_source not in ("auto", "xff", "client_ip"):
            raise typer.BadParameter("--ip-source must be auto, xff or client_ip")
        f, to_iso = _window(from_, to)
        base = _build_query(service, path, resource, query, external_only=not include_internal)
        findings: List[dict] = []
        warnings: List[str] = []

        def _progress(msg: str) -> None:
            if not is_json(ctx):
                sys.stderr.write(f"· {msg}\n")
                sys.stderr.flush()

        # 1) Overview -------------------------------------------------------
        _progress("overview")
        status_rows = _groups(_aggregate(client, base, f, to_iso, [{"aggregation": "count"}],
                                         [{"facet": "@http.status_code", **_sort(15)}]), ["@http.status_code"])
        total = sum(r["count"] for r in status_rows)
        status_codes = {str(r["@http.status_code"]): r["count"] for r in status_rows}
        err4 = sum(c for s, c in status_codes.items() if s.startswith("4"))
        err5 = sum(c for s, c in status_codes.items() if s.startswith("5"))
        overview = {"requests": total, "status_codes": status_codes,
                    "pct_4xx": round(err4 / total * 100, 2) if total else 0.0,
                    "pct_5xx": round(err5 / total * 100, 2) if total else 0.0}
        if not total:
            meta = {"query": base, "from": from_, "to": to, "overview": overview,
                    "hint": t("Sin peticiones HTTP externas (con IP de cliente) para ese filtro/rango. "
                              "Prueba --include-internal, amplía --from o escanea el ingress (p.ej. --service traefik).",
                              "No external HTTP requests (with a client IP) for that filter/range. "
                              "Try --include-internal, widen --from, or scan the ingress (e.g. --service traefik).")}
            emit(ctx, "security.endpoint_scan", [], meta=meta,
                 table_renderer=lambda: console.print(f"[yellow]{meta['hint']}[/yellow]"))
            return

        src = _resolve_ip_source(client, base, f, to_iso, ip_source)
        ip_facet = XFF_FACET if src == "xff" else CLIENT_IP_FACET

        # 2) Clients / bots by User-Agent ---------------------------------------
        _progress("user agents")
        ua_rows = _groups(_aggregate(client, _combine(base, f"{UA_FACET}:*"), f, to_iso,
                                     [{"aggregation": "count"}, {"aggregation": "cardinality", "metric": ip_facet}],
                                     [{"facet": UA_FACET, **_sort(500)}]), [UA_FACET])
        no_ua = total - sum(r["count"] for r in ua_rows)
        classes: Dict[str, Dict[str, Any]] = {}
        top_uas: List[Dict[str, Any]] = []
        for r in ua_rows:
            ua = str(r[UA_FACET] or "")
            cls, label = classify_user_agent(ua)
            ips = int(r.get("c1") or 0)
            c = classes.setdefault(cls, {"class": cls, "requests": 0, "user_agents": 0, "labels": {}})
            c["requests"] += r["count"]
            c["user_agents"] += 1
            c["labels"][label] = c["labels"].get(label, 0) + r["count"]
            top_uas.append({"user_agent": ua[:160], "class": cls, "label": label, "requests": r["count"], "ips": ips})
            sev = {"scanner": "high", "headless": "medium", "suspicious": "low"}.get(cls)
            if sev:
                findings.append({
                    "rule": f"{cls}_user_agent" if cls != "suspicious" else "anomalous_user_agent",
                    "severity": sev, "category": "attack",
                    "title": {"scanner": "Security scanner user agent",
                              "headless": "Headless/automated browser",
                              "suspicious": "Malformed/short user agent"}[cls],
                    "service": service or "", "resource": path or resource or "", "field": UA_FACET,
                    "evidence": f"{label}: {r['count']} req from {ips} IPs", "count": r["count"],
                })
            # Botnet / rotating proxies: one automated UA spread over many IPs at low rate each
            if cls in AUTOMATED_CLASSES - {"search_crawler", "social_crawler"} and ips >= botnet_min_ips:
                avg = r["count"] / max(1, ips)
                findings.append({
                    "rule": "botnet_shared_user_agent",
                    "severity": "high" if avg <= 20 else "medium", "category": "attack",
                    "title": "Same automated user agent from many client IPs (botnet / rotating proxies?)",
                    "service": service or "", "resource": path or resource or "", "field": UA_FACET,
                    "evidence": f"{label} ({cls}): {ips} IPs, {r['count']} req, avg {avg:.1f}/IP",
                    "count": r["count"], "distinct_ips": ips,
                })
        if no_ua > 0:
            classes.setdefault("empty", {"class": "empty", "requests": 0, "user_agents": 0, "labels": {}})
            classes["empty"]["requests"] += no_ua
        for c in classes.values():
            c["share_pct"] = round(c["requests"] / total * 100, 2)
            c["labels"] = dict(sorted(c["labels"].items(), key=lambda kv: -kv[1])[:5])
        automated = sum(c["requests"] for k, c in classes.items() if k in AUTOMATED_CLASSES)
        if no_ua and no_ua / total >= 0.05:
            findings.append({"rule": "anomalous_user_agent", "severity": "low", "category": "attack",
                             "title": "Requests without User-Agent", "service": service or "",
                             "resource": path or resource or "", "field": UA_FACET,
                             "evidence": f"{no_ua} req ({no_ua / total * 100:.1f}%) without UA", "count": no_ua})

        # 3) Spoofed crawlers (claims Googlebot/bingbot/Applebot from a foreign AS) --
        _progress("crawler spoofing")
        try:
            crawler_q = " OR ".join(wildcard(UA_FACET, n) for n in ("Googlebot", "bingbot", "Applebot"))
            rows = _groups(_aggregate(client, _combine(base, crawler_q), f, to_iso, [{"aggregation": "count"}],
                                      [{"facet": UA_FACET, **_sort(50)}, {"facet": AS_NAME, **_sort(20)}]),
                           [UA_FACET, AS_NAME])
            for r in rows:
                name = is_spoofed_crawler(str(r[UA_FACET] or ""), str(r[AS_NAME] or "") or None)
                if name:
                    findings.append({
                        "rule": "spoofed_crawler", "severity": "high", "category": "attack",
                        "title": f"User agent claims {name} but traffic comes from a non-{name} network",
                        "service": service or "", "resource": path or resource or "", "field": AS_NAME,
                        "evidence": f"{name} from AS '{r[AS_NAME]}': {r['count']} req", "count": r["count"],
                    })
        except ApiError as e:
            warnings.append(f"spoofing: HTTP {e.status_code}")

        # 4) Network origin (AS type / hosting providers) --------------------------
        _progress("network origin (ASN)")
        network: Dict[str, Any] = {}
        try:
            rows = _groups(_aggregate(client, _combine(base, f"{AS_NAME}:*"), f, to_iso,
                                      [{"aggregation": "count"}, {"aggregation": "cardinality", "metric": CLIENT_IP_FACET}],
                                      [{"facet": AS_NAME, **_sort(100)}, {"facet": AS_TYPE, **_sort(5)}]),
                           [AS_NAME, AS_TYPE])
            cdn_req = sum(r["count"] for r in rows if CDN_AS_RE.search(str(r[AS_NAME] or "")))
            direct = [r for r in rows if not CDN_AS_RE.search(str(r[AS_NAME] or ""))]
            direct_total = sum(r["count"] for r in direct)
            by_type: Dict[str, int] = {}
            for r in direct:
                by_type[str(r[AS_TYPE] or "unknown")] = by_type.get(str(r[AS_TYPE] or "unknown"), 0) + r["count"]
            hosting = sorted([r for r in direct if str(r[AS_TYPE] or "") == "hosting"], key=lambda r: -r["count"])
            network = {
                "via_cdn_requests": cdn_req,
                "direct_requests_with_asn": direct_total,
                "as_types": by_type,
                "top_hosting_as": [{"as": r[AS_NAME], "requests": r["count"], "ips": int(r.get("c1") or 0)}
                                   for r in hosting[:top]],
            }
            if cdn_req and cdn_req >= direct_total:
                warnings.append("most traffic arrives via a CDN: AS/geo data describes the CDN edge, "
                                "not the client (see X-Forwarded-For based IP sections)")
            hosting_req = by_type.get("hosting", 0)
            if direct_total and hosting_req / direct_total >= 0.3 and hosting_req >= 100:
                findings.append({
                    "rule": "datacenter_traffic", "severity": "medium", "category": "attack",
                    "title": "Large share of direct traffic from hosting/datacenter networks (bots/scrapers)",
                    "service": service or "", "resource": path or resource or "", "field": AS_TYPE,
                    "evidence": f"{hosting_req} req ({hosting_req / direct_total * 100:.1f}% of direct) from hosting ASNs; "
                                f"top: {', '.join(str(h['as']) for h in network['top_hosting_as'][:3])}",
                    "count": hosting_req,
                })
        except ApiError as e:
            warnings.append(f"network: HTTP {e.status_code}")

        # 5) Top client IPs ---------------------------------------------------------
        _progress(f"top client IPs ({src})")
        top_ips: List[Dict[str, Any]] = []
        cidrs: List[Dict[str, Any]] = []
        try:
            r_all = _per_ip(client, base, f, to_iso, src,
                            [{"aggregation": "count"}, {"aggregation": "cardinality", "metric": "@http.url_details.path"}],
                            limit=3000)
            r_4xx = _per_ip(client, _combine(base, "@http.status_code:[400 TO 499]"), f, to_iso, src,
                            [{"aggregation": "count"}], limit=1000)
            ips = r_all["ips"]
            for ip, c in sorted(ips.items(), key=lambda kv: -kv[1].get("c0", 0))[:top]:
                cnt = int(c.get("c0", 0))
                e4 = int(r_4xx["ips"].get(ip, {}).get("c0", 0))
                row = {"ip": ip, "requests": cnt, "share_pct": round(cnt / total * 100, 2),
                       "distinct_paths": int(c.get("c1", 0)), "pct_4xx": round(e4 / cnt * 100, 1) if cnt else 0.0}
                top_ips.append(row)
                if row["share_pct"] >= 10 and cnt >= 200:
                    findings.append({
                        "rule": "dominant_client_ip", "severity": "medium", "category": "attack",
                        "title": "One client IP generates a large share of the endpoint's traffic",
                        "service": service or "", "resource": path or resource or "", "field": ip_facet,
                        "evidence": f"{ip}: {cnt} req ({row['share_pct']}%)", "client_ip": ip, "count": cnt,
                    })
                if row["pct_4xx"] >= 50 and cnt >= 50:
                    findings.append({
                        "rule": "error_heavy_client_ip", "severity": "medium", "category": "attack",
                        "title": "Client IP whose requests mostly fail with 4xx (probing/fuzzing)",
                        "service": service or "", "resource": path or resource or "", "field": ip_facet,
                        "evidence": f"{ip}: {row['pct_4xx']}% 4xx of {cnt} req, {row['distinct_paths']} paths",
                        "client_ip": ip, "count": cnt,
                    })
            cidrs = cidr_concentration({ip: int(c.get("c0", 0)) for ip, c in ips.items()}, botnet_min_ips)
            for g in cidrs[:top]:
                findings.append({
                    "rule": "distributed_ip_range", "severity": "medium" if g["share_pct"] >= 5 else "low",
                    "category": "attack",
                    "title": "Many rotating IPs from one /16 hitting the endpoint (distributed scraping/scan?)",
                    "service": service or "", "resource": path or resource or "", "field": ip_facet,
                    "evidence": f"{g['prefix']}: {g['ips']} IPs, {g['requests']} req ({g['share_pct']}%), avg {g['avg_per_ip']}/IP",
                    "client_ip": g["prefix"], "count": g["requests"],
                })
            if not ips:
                warnings.append(
                    "no usable client IP on these spans (none recorded, or only CDN edges); "
                    "scan the ingress instead, e.g. --service traefik --query '@http.url_details.host:<host>'"
                )
            if r_all["cdn_edge_ips"]:
                warnings.append(f"{r_all['cdn_edge_ips']} CDN edge IPs ignored in per-IP analysis")
            if r_all["truncated"]:
                warnings.append("top-3000 IP groups analysed; long tail not included")
        except ApiError as e:
            warnings.append(f"ips: HTTP {e.status_code}")

        # 6) Headers + URL payloads on a sample -------------------------------------
        _progress(f"headers on a {sample}-span sample")
        header_keys: Dict[str, int] = {}
        if sample > 0:
            items = _search_spans(client, base, f, to_iso, sample)
            ua_attack_q = " OR ".join(wildcard(UA_FACET, p) for p in ("${", "<script", "union", "UNION", "sleep(", "../", "() {"))
            items += _search_spans(client, _combine(base, ua_attack_q), f, to_iso, 50)
            for raw in items:
                v = span_view(raw)
                for k in v:
                    if k.startswith("http.request.headers."):
                        hk = k[len("http.request.headers."):]
                        header_keys[hk] = header_keys.get(hk, 0) + 1
                findings.extend(header_findings(v))
            findings.extend(scan_spans(items))
            if set(header_keys) <= {"user-agent", "x-forwarded-for"}:
                warnings.append(
                    "only user-agent/x-forwarded-for headers are captured; set DD_TRACE_HEADER_TAGS "
                    "(e.g. referer, x-real-ip, true-client-ip, x-original-url, x-rewrite-url, host) "
                    "for deeper header analysis"
                )

        findings = group_findings(findings)
        meta = {
            "query": base, "from": from_, "to": to, "ip_source": src,
            "severity": severity_summary(findings),
            "overview": overview,
            "automated_pct": round(automated / total * 100, 2) if total else 0.0,
            "clients": sorted(classes.values(), key=lambda c: -c["requests"]),
            "top_user_agents": sorted(top_uas, key=lambda u: -u["requests"])[:top],
            "network": network,
            "top_ips": top_ips,
            "ip_ranges": cidrs[:top],
            "headers_captured": header_keys,
        }
        if warnings:
            meta["warnings"] = warnings

        def _render() -> None:
            console.print(f"[bold]{base}[/bold]")
            console.print(
                f"requests={total}  4xx={overview['pct_4xx']}%  5xx={overview['pct_5xx']}%  "
                f"automated≈{meta['automated_pct']}%  ip_source={src}"
            )
            tbl = new_table("Clients by user-agent class")
            for col in ("class", "requests", "share", "top labels"):
                tbl.add_column(col, no_wrap=col != "top labels")
            for c in meta["clients"]:
                tbl.add_row(c["class"], str(c["requests"]), f"{c['share_pct']}%",
                            ", ".join(f"{k}({v})" for k, v in c["labels"].items()))
            console.print(tbl)
            if top_ips:
                tbl = new_table(f"Top client IPs ({src})")
                for col in ("ip", "requests", "share", "paths", "4xx"):
                    tbl.add_column(col, no_wrap=True)
                for r in top_ips:
                    tbl.add_row(r["ip"], str(r["requests"]), f"{r['share_pct']}%", str(r["distinct_paths"]), f"{r['pct_4xx']}%")
                console.print(tbl)
            if network.get("top_hosting_as"):
                tbl = new_table("Top hosting/datacenter ASNs (direct traffic)")
                for col in ("as", "requests", "ips"):
                    tbl.add_column(col)
                for h in network["top_hosting_as"]:
                    tbl.add_row(str(h["as"]), str(h["requests"]), str(h["ips"]))
                console.print(tbl)
            _render_findings("Endpoint scan findings", findings, meta)
            for w in warnings:
                console.print(f"[yellow]warning: {w}[/yellow]")

        emit(ctx, "security.endpoint_scan", findings, meta=meta, table_renderer=_render)
    except Exception as exc:
        fail(ctx, "security.endpoint_scan", exc, debug)
