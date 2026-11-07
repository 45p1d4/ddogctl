"""
Security (infosec) rules evaluated over APM spans.

Pure functions, no I/O: `scan_span()` takes one span as returned by
/api/v2/spans/events[/search] and returns a list of findings. Evidence is
always redacted — a finding says *where* a secret is, never what it is.

Rule families (OWASP-style):
- exposure:  secrets/session IDs in URLs, credentials in span tags, token
             patterns (JWT, cloud keys, private keys), PII in URLs
- attack:    injection payloads (SQLi/XSS/traversal/command/JNDI), scanner
             probe paths, scanner user agents, SQL errors triggered by input
- transport: cleartext HTTP calls to non-internal hosts
"""
from __future__ import annotations

import ipaddress
import re
import urllib.parse as up
from typing import Any, Dict, Iterable, List, Optional, Tuple

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def mask(value: Any, keep: int = 3) -> str:
    s = str(value)
    if len(s) <= keep + 2:
        return "*" * len(s)
    return f"{s[:keep]}…({len(s)} chars)"


def mask_email(s: str) -> str:
    local, _, domain = s.partition("@")
    return f"{local[:1]}***@{domain}"


SENSITIVE_PARAM_RE = re.compile(
    r"^(?:.*[_-])?(?:access[_-]?token|id[_-]?token|refresh[_-]?token|token|auth|authorization|"
    r"api[_-]?key|apikey|x[_-]?api[_-]?key|secret|client[_-]?secret|password|passwd|pwd|pass|"
    r"signature|sig|jwt|bearer|otp|pin|ssn|"
    r"session|sessionid|session[_-]?id|sub[_-]?session[_-]?id|jsessionid|phpsessid|sid)$",
    re.I,
)
SESSION_PARAM_RE = re.compile(r"session|sid$|jsessionid|phpsessid", re.I)


def redact_url(url: str) -> str:
    """Mask the value of every sensitive query parameter (and ;jsessionid=)."""
    try:
        parts = up.urlsplit(url)
    except ValueError:
        return mask(url, 20)
    # Rebuild the query by hand: keep the original encoding of harmless params
    # and put the readable mask in place of sensitive values.
    pairs = []
    for chunk in parts.query.split("&") if parts.query else []:
        k, sep, v = chunk.partition("=")
        if v and SENSITIVE_PARAM_RE.match(up.unquote_plus(k)) and not is_obfuscated(up.unquote_plus(v)):
            v = mask(up.unquote_plus(v))
        pairs.append(f"{k}{sep}{v}")
    path = re.sub(r"(;jsessionid=)[^/?#;]+", lambda m: m.group(1) + "***", parts.path, flags=re.I)
    base = f"{parts.scheme}://{parts.netloc}{path}" if parts.scheme else path
    return base + ("?" + "&".join(pairs) if pairs else "")


OBFUSCATED_VALUES = {"redacted", "?", "<redacted>", "[redacted]", "***", "xxx", "filtered", "[filtered]"}


def is_obfuscated(value: str) -> bool:
    """Values already scrubbed by the tracer/agent obfuscation are not a leak."""
    v = (value or "").strip().strip('"').lower()
    return v in OBFUSCATED_VALUES or set(v) <= {"*", "x"} and len(v) >= 3


# ---------------------------------------------------------------------------
# Span flattening
# ---------------------------------------------------------------------------


def flatten(d: Any, prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                out.update(flatten(v, key + "."))
            else:
                out[key] = v
    return out


def span_view(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Merge span attributes: top-level + custom.* (custom wins) into one flat map."""
    a = (raw or {}).get("attributes") or {}
    flat = {k: v for k, v in flatten(a).items() if not k.startswith("custom.")}
    flat.update(flatten(a.get("custom") or {}))
    flat["_span_id"] = a.get("span_id") or raw.get("id")
    flat["_trace_id"] = a.get("trace_id")
    return flat


def _get(v: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if v.get(k) not in (None, ""):
            return v[k]
    return None


def _decode(s: str) -> str:
    # Attack payloads are often double-encoded.
    once = up.unquote_plus(s)
    return up.unquote_plus(once)


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

INJECTION_PATTERNS: List[Tuple[str, str, re.Pattern]] = [
    ("sqli", "UNION SELECT", re.compile(r"union(\s|/\*.*?\*/)+(all(\s|/\*.*?\*/)+)?select", re.I)),
    ("sqli", "time-based (SLEEP/pg_sleep/WAITFOR/BENCHMARK)",
     re.compile(r"\b(sleep|pg_sleep)\s*\(\s*\d+|waitfor\s+delay\s+'|benchmark\s*\(\s*\d+", re.I)),
    ("sqli", "FROM DUAL / information_schema", re.compile(r"\bfrom\s+dual\b|information_schema\.", re.I)),
    ("sqli", "boolean tautology", re.compile(r"['\"]\s*(or|and)\s+['\"]?\w+['\"]?\s*=\s*['\"]?\w+|\bor\s+1\s*=\s*1\b", re.I)),
    ("sqli", "stacked query", re.compile(r";\s*(drop|delete|insert|update|exec|shutdown)\s", re.I)),
    ("sqli", "SELECT … FROM in parameter", re.compile(r"[=(]\s*\(?\s*select\s+[\w*,\s()]+\s+from\s+\w", re.I)),
    ("xss", "script/event-handler injection",
     re.compile(r"<\s*script|javascript\s*:|\bon(error|load|mouseover|focus)\s*=|<\s*(img|svg|iframe)[^>]*\bsrc", re.I)),
    ("traversal", "path traversal", re.compile(r"(\.\./|\.\.\\){2,}|/etc/passwd|/etc/shadow|win\.ini|boot\.ini", re.I)),
    ("cmdi", "command injection", re.compile(r"[;|`]\s*(cat|id|uname|whoami|wget|curl|nc|bash|sh)\b|\$\(\s*(id|whoami|curl|wget)", re.I)),
    ("jndi", "JNDI/Log4Shell lookup", re.compile(r"\$\{\s*(jndi|lower|upper|env|sys)\s*:", re.I)),
    ("ssti", "template injection", re.compile(r"\{\{\s*\d+\s*\*\s*\d+\s*\}\}|\$\{\s*\d+\s*\*\s*\d+\s*\}")),
]

PROBE_PATH_RE = re.compile(
    r"(^|/)(\.env(\.\w+)?|\.git/(config|HEAD)|\.svn/|\.hg/|\.DS_Store|\.aws/credentials|\.ssh/|"
    r"wp-admin|wp-login\.php|xmlrpc\.php|wp-config\.php|phpmyadmin|pma/|adminer\.php|"
    r"server-status|actuator/(env|heapdump|threaddump|gateway|mappings|configprops|beans|loggers|shutdown|jolokia)|jolokia|"
    r"vendor/phpunit|cgi-bin/|boaform|HNAP1|"
    r"manager/html|solr/admin|console/login|_ignition|telescope|"
    r"config\.(json|php|yml)|backup\.(zip|tar|sql)|dump\.sql|id_rsa)(\b|$)",
    re.I,
)

SCANNER_UA_RE = re.compile(
    r"sqlmap|nikto|nuclei|zgrab|masscan|gobuster|dirbuster|dirb\b|wpscan|acunetix|nessus|"
    r"nmap|burp|ffuf|feroxbuster|wfuzz|openvas|w3af|arachni|zap/|hydra|jaeles|interactsh",
    re.I,
)

SQL_ERROR_RE = re.compile(
    r"you have an error in your sql syntax|SQLSyntaxErrorException|ORA-009(0[0-9]|1[0-9]|3[0-9])|"
    r"ORA-01756|unterminated quoted string|syntax error at or near|quoted string not properly terminated|"
    r"SQLSTATE\[42|Incorrect syntax near|unclosed quotation mark",
    re.I,
)

TOKEN_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}")),
    ("AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Stripe live key", re.compile(r"\b(sk|rk)_live_[A-Za-z0-9]{16,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.I)),
]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# "logo@2x.png" / "icon@3x.webp" are asset names, not emails.
NOT_EMAIL_TLD_RE = re.compile(r"\.(png|jpe?g|gif|svg|webp|avif|ico|bmp|js|mjs|css|map|json|woff2?|ttf|otf|eot|mp4|webm|pdf)$", re.I)
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")

CREDENTIAL_KEY_RE = re.compile(
    r"(^|\.)(authorization|proxy-authorization|cookie|set-cookie|x-api-key|x-auth-token|x-access-token|"
    r"password|passwd|secret|client_secret|api_key|apikey|private_key)$",
    re.I,
)
# Keys that legitimately contain these words but hold no secret material.
CREDENTIAL_KEY_ALLOW = re.compile(r"^_dd\.|^git\.|\.(enabled|type|length|count|present)$", re.I)

INTERNAL_HOST_SUFFIXES = (".svc", ".cluster.local", ".internal", ".local", ".localdomain", ".lan", ".corp", ".consul")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def is_internal_host(host: Optional[str]) -> bool:
    if not host:
        return True
    h = host.split(":")[0].strip("[]").lower()
    if h in ("localhost",) or "." not in h:
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        pass
    return h.endswith(INTERNAL_HOST_SUFFIXES)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _finding(v: Dict[str, Any], rule: str, severity: str, category: str, title: str,
             field: str, evidence: str) -> Dict[str, Any]:
    return {
        "rule": rule,
        "severity": severity,
        "category": category,
        "title": title,
        "service": v.get("service") or "",
        "resource": redact_url(str(v.get("resource_name") or ""))[:120],
        "field": field,
        "evidence": evidence[:240],
        "span_id": v.get("_span_id"),
        "trace_id": v.get("_trace_id"),
        "client_ip": _get(v, "http.client_ip", "network.client.ip") or "",
    }


def _rule_url(v: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    url = _get(v, "http.url")
    if not isinstance(url, str) or not url:
        return out
    try:
        parts = up.urlsplit(url)
        params = up.parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        parts, params = None, []

    # Secrets / session identifiers in the query string
    for k, val in params:
        if val and len(val) >= 6 and SENSITIVE_PARAM_RE.match(k) and not is_obfuscated(val):
            is_session = bool(SESSION_PARAM_RE.search(k))
            out.append(_finding(
                v, "session_id_in_url" if is_session else "secret_in_url",
                "medium" if is_session else "high", "exposure",
                f"{'Session identifier' if is_session else 'Credential/token'} in URL query parameter '{k}'",
                "http.url", redact_url(url),
            ))
    if parts is not None and re.search(r";jsessionid=", parts.path, re.I):
        out.append(_finding(v, "session_id_in_url", "medium", "exposure",
                            "Session identifier in URL path (;jsessionid=)", "http.url", redact_url(url)))

    decoded = _decode(url)
    # PII in URL
    m = next((e for e in EMAIL_RE.finditer(decoded) if not NOT_EMAIL_TLD_RE.search(e.group(0))), None)
    if m:
        out.append(_finding(v, "pii_in_url", "medium", "exposure", "Email address in URL",
                            "http.url", mask_email(m.group(0))))
    for cm in CARD_RE.finditer(decoded):
        digits = re.sub(r"\D", "", cm.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits) and len(set(digits)) > 1:
            out.append(_finding(v, "pii_in_url", "high", "exposure",
                                "Payment card number (Luhn-valid) in URL", "http.url", f"****{digits[-4:]}"))
            break

    # Attack payloads
    # Only the request target (path + query) — never the host name.
    target = _decode(parts.path + "?" + parts.query) if parts else decoded
    for family, label, rx in INJECTION_PATTERNS:
        if rx.search(target):
            out.append(_finding(v, f"injection_{family}", "high", "attack",
                                f"Injection payload in request ({label})", "http.url",
                                redact_url(url)[:200]))
    path = (parts.path if parts else "") or str(_get(v, "http.url_details.path") or "")
    if PROBE_PATH_RE.search(path):
        out.append(_finding(v, "scanner_probe_path", "medium", "attack",
                            "Request to a path commonly probed by vulnerability scanners", "http.url", path[:200]))
    return out


def _rule_user_agent(v: Dict[str, Any]) -> List[Dict[str, Any]]:
    ua = _get(v, "http.useragent", "http.request.headers.user-agent")
    if isinstance(ua, str) and SCANNER_UA_RE.search(ua):
        return [_finding(v, "scanner_user_agent", "high", "attack",
                         "Request from a known security-scanner user agent", "http.useragent", ua[:160])]
    return []


def _rule_sql_error(v: Dict[str, Any]) -> List[Dict[str, Any]]:
    for k in ("error.message", "error.msg", "error.type", "error.stack"):
        val = v.get(k)
        if isinstance(val, str) and SQL_ERROR_RE.search(val):
            m = SQL_ERROR_RE.search(val)
            return [_finding(v, "sql_error_from_input", "medium", "attack",
                             "SQL syntax error raised while handling the request (possible injection point)",
                             k, val[max(0, m.start() - 40): m.end() + 60])]
    return []


def _rule_credentials_in_tags(v: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for k, val in v.items():
        if k.startswith("_") or not isinstance(val, str) or not val:
            continue
        if is_obfuscated(val):
            continue
        if CREDENTIAL_KEY_RE.search(k) and not CREDENTIAL_KEY_ALLOW.search(k) and len(val) >= 6:
            out.append(_finding(v, "credential_in_span_tag", "high", "exposure",
                                f"Credential-bearing value captured in span tag '{k}'", k, mask(val)))
            continue
        if k == "http.url" or k.startswith("http.url_details."):
            continue  # part of the URL: reported once by _rule_url
        for label, rx in TOKEN_PATTERNS:
            m = rx.search(val)
            if m:
                out.append(_finding(v, "token_pattern_in_span", "high", "exposure",
                                    f"{label} found in span tag '{k}'", k, mask(m.group(0), 6)))
                break
    return out


def _rule_cleartext(v: Dict[str, Any]) -> List[Dict[str, Any]]:
    kind = str(v.get("span.kind") or "").lower()
    url = _get(v, "http.url")
    scheme = str(_get(v, "http.url_details.scheme") or (url.split(":", 1)[0] if isinstance(url, str) and ":" in url else "")).lower()
    if kind != "client" or scheme != "http":
        return []
    host = _get(v, "http.url_details.host", "out.host", "peer.hostname", "http.host")
    if is_internal_host(str(host) if host else None):
        return []
    return [_finding(v, "cleartext_http_external", "medium", "transport",
                     f"Outbound cleartext HTTP call to external host {host}", "http.url",
                     redact_url(url)[:200] if isinstance(url, str) else str(host))]


SPAN_RULES = (_rule_url, _rule_user_agent, _rule_sql_error, _rule_credentials_in_tags, _rule_cleartext)


def scan_span(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    v = span_view(raw)
    findings: List[Dict[str, Any]] = []
    for rule in SPAN_RULES:
        findings.extend(rule(v))
    return findings


def scan_spans(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for raw in items or []:
        for f in scan_span(raw):
            key = (f["rule"], f["span_id"], f["field"])
            if key not in seen:
                seen.add(key)
                out.append(f)
    return sort_findings(out)


def sort_findings(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f.get("severity", "info"), 9), f.get("rule", "")))


def severity_summary(findings: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    out = {k: 0 for k in SEVERITY_ORDER}
    for f in findings:
        out[f.get("severity", "info")] = out.get(f.get("severity", "info"), 0) + 1
    return out


# ---------------------------------------------------------------------------
# Aggregate helpers (discover)
# ---------------------------------------------------------------------------


def ip_prefix(ip: str, v4_bits: int = 16, v6_bits: int = 48) -> Optional[str]:
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return None
    bits = v4_bits if addr.version == 4 else v6_bits
    return str(ipaddress.ip_network(f"{addr}/{bits}", strict=False))


def cidr_concentration(ip_counts: Dict[str, int], min_ips: int, v4_bits: int = 16) -> List[Dict[str, Any]]:
    """Group public client IPs by prefix; keep prefixes with >= min_ips distinct IPs."""
    groups: Dict[str, Dict[str, Any]] = {}
    total = sum(ip_counts.values()) or 1
    for ip, cnt in ip_counts.items():
        pfx = ip_prefix(ip, v4_bits)
        if not pfx:
            continue
        g = groups.setdefault(pfx, {"prefix": pfx, "ips": 0, "requests": 0, "max_per_ip": 0})
        g["ips"] += 1
        g["requests"] += cnt
        g["max_per_ip"] = max(g["max_per_ip"], cnt)
    out = []
    for g in groups.values():
        if g["ips"] >= min_ips:
            g["share_pct"] = round(g["requests"] / total * 100, 2)
            g["avg_per_ip"] = round(g["requests"] / g["ips"], 1)
            out.append(g)
    return sorted(out, key=lambda g: g["requests"], reverse=True)


_DD_SPECIAL = re.compile(r'([+\-=&|><!(){}\[\]^"~*?:\\/\s])')


def dd_escape(s: str) -> str:
    """Escape a literal for a Datadog search wildcard term (`@field:*<literal>*`)."""
    return _DD_SPECIAL.sub(r"\\\1", s)


def wildcard(field: str, *parts: str) -> str:
    return f"{field}:*" + "*".join(dd_escape(p) for p in parts) + "*"


# Server-side prefilters for `security discover`. Each is confirmed client-side
# by scan_span() on a sample, so broad wildcards are fine here.
SIGNATURE_QUERIES: List[Tuple[str, str]] = [
    ("injection_sqli", " OR ".join([
        wildcard("@http.url", "union", "select"), wildcard("@http.url", "UNION", "SELECT"),
        wildcard("@http.url", "sleep("), wildcard("@http.url", "SLEEP("),
        wildcard("@http.url", "sleep%28"), wildcard("@http.url", "SLEEP%28"),
        wildcard("@http.url", "DUAL"), wildcard("@http.url", "information_schema"),
        wildcard("@http.url", "WAITFOR"), wildcard("@http.url", "pg_sleep"),
    ])),
    ("injection_traversal", " OR ".join([
        wildcard("@http.url", "../"), wildcard("@http.url", "..%2F"), wildcard("@http.url", "%2e%2e"),
        wildcard("@http.url", "etc/passwd"),
    ])),
    ("injection_xss", " OR ".join([
        wildcard("@http.url", "<script"), wildcard("@http.url", "%3Cscript"), wildcard("@http.url", "javascript:"),
        wildcard("@http.url", "onerror="),
    ])),
    ("injection_jndi", " OR ".join([wildcard("@http.url", "${jndi"), wildcard("@http.url", "%24%7Bjndi")])),
    ("scanner_probe_path", " OR ".join([
        wildcard("@http.url", "/.env"), wildcard("@http.url", "/.git/"), wildcard("@http.url", "wp-login"),
        wildcard("@http.url", "wp-admin"), wildcard("@http.url", "phpmyadmin"), wildcard("@http.url", "xmlrpc.php"),
        wildcard("@http.url", "/actuator"), wildcard("@http.url", "server-status"), wildcard("@http.url", "phpunit"),
        wildcard("@http.url", "/cgi-bin/"),
    ])),
    ("scanner_user_agent", " OR ".join(
        wildcard("@http.useragent", ua) for ua in
        ("sqlmap", "nikto", "Nikto", "nuclei", "Nuclei", "zgrab", "masscan", "gobuster", "wpscan", "WPScan",
         "acunetix", "Acunetix", "Nessus", "nmap", "Nmap", "ffuf", "feroxbuster", "Fuzz")
    )),
    ("secret_in_url", " OR ".join(
        wildcard("@http.url", f"{p}=") for p in
        ("token", "access_token", "api_key", "apikey", "password", "passwd", "secret", "client_secret", "jwt")
    )),
    ("session_id_in_url", " OR ".join(
        wildcard("@http.url", p) for p in ("session=", "sessionid=", "SessionId=", "sessionId=", "jsessionid=", "PHPSESSID=")
    )),
    ("sql_error_from_input", " OR ".join([
        wildcard("@error.message", "SQL syntax"), wildcard("@error.message", "SQLSyntaxErrorException"),
        wildcard("@error.message", "ORA-00933"), wildcard("@error.message", "ORA-00936"),
        wildcard("@error.message", "ORA-01756"), wildcard("@error.message", "syntax error at or near"),
        wildcard("@error.type", "SQLSyntaxErrorException"),
    ])),
]


# ---------------------------------------------------------------------------
# Client IP resolution behind CDNs / proxies
# ---------------------------------------------------------------------------

# Approximate public ranges of the big CDNs. When a "client IP" falls here it is
# almost certainly an edge node, not the real client: per-IP analytics on it
# are meaningless (thousands of users share a few edge IPs).
CDN_RANGES = {
    "akamai": ["2.16.0.0/13", "23.0.0.0/12", "23.32.0.0/11", "23.64.0.0/14", "23.192.0.0/11",
               "72.246.0.0/15", "88.221.0.0/16", "92.122.0.0/15", "95.100.0.0/15", "96.6.0.0/15",
               "104.64.0.0/10", "184.24.0.0/13", "184.50.0.0/15", "184.84.0.0/14"],
    "cloudflare": ["173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
                   "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
                   "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
                   "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22"],
    "fastly": ["151.101.0.0/16", "199.232.0.0/16"],
}
_CDN_NETS = [(name, ipaddress.ip_network(c)) for name, cidrs in CDN_RANGES.items() for c in cidrs]


def cdn_of(ip: str) -> Optional[str]:
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    for name, net in _CDN_NETS:
        if addr.version == net.version and addr in net:
            return name
    return None


def _is_public_ip(s: str) -> bool:
    try:
        a = ipaddress.ip_address(s)
    except ValueError:
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast)


def client_ip_from_xff(value: Any) -> Optional[str]:
    """
    Real client from an X-Forwarded-For value: the left-most public IP that is
    not a CDN edge. Accepts plain 'a, b, c' or the JSON-ish '["a, b", "c"]'
    form Datadog stores for multi-valued headers.
    """
    if value is None:
        return None
    raw = value if isinstance(value, str) else ",".join(map(str, value)) if isinstance(value, list) else str(value)
    tokens = [t.strip().strip("\"'[] ") for t in re.split(r"[,\s]+", raw)]
    for tok in tokens:
        ip = tok.split("%")[0]
        if ip.count(":") == 1 and "." in ip:  # 1.2.3.4:port
            ip = ip.split(":")[0]
        if _is_public_ip(ip) and not cdn_of(ip):
            return ip
    return None


def group_findings(findings: List[Dict[str, Any]], max_samples: int = 3) -> List[Dict[str, Any]]:
    """Collapse findings with the same (rule, service, resource, field) into one row with a count."""
    groups: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for f in findings:
        key = (f.get("rule"), f.get("service"), f.get("resource"), f.get("field"), f.get("client_ip") if not f.get("span_id") else None)
        g = groups.get(key)
        if g is None:
            g = dict(f)
            g["occurrences"] = 0
            g["sample_span_ids"] = []
            g["sample_trace_ids"] = []
            groups[key] = g
        g["occurrences"] += int(f.get("occurrences") or 1)
        for src, dst in (("span_id", "sample_span_ids"), ("trace_id", "sample_trace_ids")):
            val = f.get(src)
            if val and val not in g[dst] and len(g[dst]) < max_samples:
                g[dst].append(val)
    out = []
    for g in groups.values():
        g.pop("span_id", None)
        g.pop("trace_id", None)
        out.append(g)
    return sort_findings(out)


# ---------------------------------------------------------------------------
# Client / bot classification (endpoint-scan)
# ---------------------------------------------------------------------------

UA_CLASSES: List[Tuple[str, re.Pattern]] = [
    ("scanner", SCANNER_UA_RE),
    ("headless", re.compile(r"HeadlessChrome|PhantomJS|Puppeteer|Playwright|Selenium|webdriver|Electron/", re.I)),
    ("ai_crawler", re.compile(
        r"GPTBot|ChatGPT-User|OAI-SearchBot|ClaudeBot|Claude-Web|anthropic-ai|CCBot|PerplexityBot|"
        r"Bytespider|Amazonbot|Google-Extended|cohere-ai|Diffbot|ImagesiftBot|Omgili|YouBot|Meta-ExternalAgent", re.I)),
    ("search_crawler", re.compile(
        r"Googlebot|bingbot|Applebot|YandexBot|DuckDuckBot|Baiduspider|Slurp|Sogou|SeznamBot|Qwantify|"
        r"AdsBot-Google|Mediapartners-Google|Google-InspectionTool|GoogleOther|Storebot-Google|PetalBot", re.I)),
    ("social_crawler", re.compile(
        r"facebookexternalhit|FacebookBot|meta-webindexer|Twitterbot|LinkedInBot|Slackbot|Discordbot|"
        r"WhatsApp|TelegramBot|Pinterest|redditbot|SkypeUriPreview", re.I)),
    ("seo_crawler", re.compile(r"AhrefsBot|SemrushBot|MJ12bot|DotBot|BLEXBot|Screaming Frog|SiteAuditBot|DataForSeoBot|serpstatbot", re.I)),
    ("monitoring", re.compile(r"Datadog|Pingdom|UptimeRobot|StatusCake|NewRelicPinger|Site24x7|ELB-HealthChecker|kube-probe|GoogleHC|Prometheus", re.I)),
    ("http_library", re.compile(
        r"^(python-requests|python-urllib|aiohttp|httpx|curl|Wget|Go-http-client|go-resty|Java/|okhttp|"
        r"Apache-HttpClient|axios|node-fetch|undici|got |libwww-perl|Scrapy|PostmanRuntime|insomnia|"
        r"RestSharp|Dalvik|CFNetwork|Ruby|PHP|Guzzle|reqwest|HTTPie|node(-fetch)?(/|$)|Deno/|Bun/)", re.I)),
    ("generic_bot", re.compile(r"bot\b|crawler|spider|scrap|fetch|archiver|harvest", re.I)),
    ("browser", re.compile(r"Mozilla/5\.0 .*?(Edg|OPR|Firefox|CriOS|Chrome|Version(?=/[\d.]+ .*Safari)|Safari)/", re.I)),
]

# Claimed crawler -> AS names it must come from (anything else is a spoof).
CRAWLER_AS = {
    "Googlebot": ("google",),
    "bingbot": ("microsoft",),
    "Applebot": ("apple",),
}

CDN_AS_RE = re.compile(r"akamai|cloudflare|fastly|amazon.*cloudfront|edgecast|limelight|stackpath|imperva|incapsula", re.I)

HEADER_ATTACK_RE = re.compile(
    r"\$\{\s*(jndi|env|sys|lower|upper)\s*:|<\s*script|union(\s|/\*.*?\*/)+select|\bsleep\s*\(\s*\d|"
    r"\.\./\.\./|/etc/passwd|\(\)\s*\{\s*:;\s*\}|[\r\n]|%0d%0a|\bor\s+1\s*=\s*1\b",
    re.I,
)


def classify_user_agent(ua: Optional[str]) -> Tuple[str, str]:
    """(class, label) for a raw User-Agent string."""
    if ua is None or not str(ua).strip() or str(ua).strip() in ("-", "null", "undefined"):
        return "empty", "(no user agent)"
    s = str(ua).strip()
    for cls, rx in UA_CLASSES:
        m = rx.search(s)
        if m:
            if cls != "browser":
                return cls, m.group(0).strip(" /")
            if re.search(r"Edg/", s):
                return cls, "Edge"
            return cls, {"Version": "Safari", "CriOS": "Chrome"}.get(m.group(1), m.group(1))
    if len(s) < 12:
        return "suspicious", s
    return "other", s[:40]


AUTOMATED_CLASSES = {"scanner", "headless", "ai_crawler", "search_crawler", "social_crawler", "seo_crawler",
                     "http_library", "generic_bot", "empty", "suspicious"}


def claimed_crawler(ua: str) -> Optional[str]:
    for name in CRAWLER_AS:
        if name.lower() in (ua or "").lower():
            return name
    return None


def is_spoofed_crawler(ua: str, as_name: Optional[str]) -> Optional[str]:
    """Return the claimed crawler name when the source AS cannot belong to it."""
    name = claimed_crawler(ua)
    if not name or not as_name or CDN_AS_RE.search(as_name):
        return None  # unknown origin (behind a CDN) -> cannot judge
    if any(k in as_name.lower() for k in CRAWLER_AS[name]):
        return None
    return name


def header_findings(v: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Malicious / anomalous request headers captured on a span."""
    out: List[Dict[str, Any]] = []
    for k, val in v.items():
        if not k.startswith("http.request.headers.") or val in (None, ""):
            continue
        hname = k[len("http.request.headers."):]
        sval = val if isinstance(val, str) else str(val)
        if HEADER_ATTACK_RE.search(sval):
            out.append(_finding(v, "malicious_header", "high", "attack",
                                f"Attack payload in request header '{hname}'", k, sval[:160].replace("\r", "\r").replace("\n", "\n")))
        if hname == "x-forwarded-for":
            hops = [h.strip().strip("\"'[] ") for h in re.split(r"[,\s]+", sval) if h.strip().strip("\"'[] ")]
            bad = [h for h in hops if not _valid_ip(h)]
            if bad:
                out.append(_finding(v, "xff_spoofing", "medium", "attack",
                                    "X-Forwarded-For contains non-IP values (header injection/spoofing)", k, ", ".join(bad)[:120]))
            elif len(hops) >= 2 and not _is_public_ip(hops[0]) and any(_is_public_ip(h) for h in hops[1:]):
                out.append(_finding(v, "xff_spoofing", "medium", "attack",
                                    "X-Forwarded-For starts with a private/loopback IP followed by public hops "
                                    "(client-supplied value, possible IP-allowlist bypass)", k, hops[0]))
            if len(hops) >= 6:
                out.append(_finding(v, "xff_long_chain", "low", "attack",
                                    f"Unusually long X-Forwarded-For chain ({len(hops)} hops)", k, f"{len(hops)} hops"))
    ua = _get(v, "http.useragent", "http.request.headers.user-agent")
    cls, label = classify_user_agent(ua)
    if cls in ("empty", "suspicious") and str(v.get("span.kind") or "server") != "client":
        out.append(_finding(v, "anomalous_user_agent", "low", "attack",
                            "Missing or malformed User-Agent", "http.useragent", label))
    return out


def _valid_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s.split("%")[0])
        return True
    except ValueError:
        return s.lower() in ("unknown",)
