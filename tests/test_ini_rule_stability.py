"""INI compatibility keeps rule targets and provider formats unambiguous."""

from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from app.core.parsers.clash import clash_to_ir
from app.core.platforms.ini import redirect_unavailable_target
from app.core.platforms.shadowrocket import build_shadowrocket_config
from app.core.platforms.surge import build_surge_config
from app.core.policy_workspace import config_to_workspace, workspace_to_dict
from app.main import app


GOOD = clash_to_ir({
    "name": "Good", "type": "ss", "server": "ss.example.com", "port": 443,
    "cipher": "aes-128-gcm", "password": "test",
})
BAD = clash_to_ir({
    "name": "Bad", "type": "vless", "server": "vless.example.com", "port": 443,
    "uuid": "11111111-1111-4111-8111-111111111111",
})


def test_surge_compile_rejects_removed_final_target_and_keeps_dns_failed():
    workspace = config_to_workspace({
        "proxy-groups": [{"name": "Gone", "type": "select", "proxies": ["Bad"]}],
        "rules": ["FINAL,Gone,dns-failed"],
    }, [GOOD, BAD])

    response = TestClient(app).post("/compile", json={
        "target": "surge", "workspace": workspace_to_dict(workspace),
    })

    assert response.status_code == 200, response.text
    assert "FINAL,REJECT,dns-failed" in response.text
    assert "FINAL,Gone" not in response.text


def test_surge_compile_does_not_promote_an_option_to_rule_target():
    workspace = config_to_workspace({
        "proxy-groups": [{"name": "Gone", "type": "select", "proxies": ["Bad"]}],
        "rules": [
            "IP-CIDR,198.51.100.0/24,Gone,no-resolve,notification-text=Denied",
            "FINAL,Good,dns-failed",
        ],
    }, [GOOD, BAD])

    response = TestClient(app).post("/compile", json={
        "target": "surge", "workspace": workspace_to_dict(workspace),
    })

    assert response.status_code == 200, response.text
    assert "IP-CIDR,198.51.100.0/24,REJECT,no-resolve" in response.text
    assert "FINAL,Good,dns-failed" in response.text


@pytest.mark.parametrize("rule", [
    r"URL-REGEX,^http://example\.com/a\(b$,Good",
    r'URL-REGEX,"^http://example\.com/(a|b),?c",Good',
])
def test_surge_builder_preserves_regex_payload_punctuation(rule):
    output, warnings = build_surge_config([GOOD], [], [rule, "MATCH,DIRECT"], {})

    assert rule in output.splitlines()
    assert not warnings


@pytest.mark.parametrize("rule,expected", [
    ("FINAL,Gone,dns-failed,notification-text=Denied", "FINAL,REJECT,dns-failed,notification-text=Denied"),
    ("IP-CIDR,198.51.100.0/24,Gone,no-resolve,notification-text=Denied",
     "IP-CIDR,198.51.100.0/24,REJECT,no-resolve,notification-text=Denied"),
    ("AND,((DOMAIN,Gone),(NOT,((DOMAIN-SUFFIX,safe.example)))),Gone,no-resolve,notification-text=Denied",
     "AND,((DOMAIN,Gone),(NOT,((DOMAIN-SUFFIX,safe.example)))),REJECT,no-resolve,notification-text=Denied"),
    ("DOMAIN,example.com,Good,notification-text=Gone", "DOMAIN,example.com,Good,notification-text=Gone"),
    (" DOMAIN , example.com , Good ", " DOMAIN , example.com , Good "),
])
def test_unavailable_target_cleanup_distinguishes_match_target_and_options(rule, expected):
    assert redirect_unavailable_target(rule, {"Gone"}) == expected


@pytest.mark.parametrize("compile_config", [build_surge_config, build_shadowrocket_config])
@pytest.mark.parametrize("url,source_format", [
    ("https://example.com/rules.yaml?version=1", ""),
    ("https://example.com/rules.yml#part", ""),
    ("https://example.com/rules.mrs?version=1#part", ""),
    ("https://example.com/download?version=1", "yaml"),
    ("https://example.com/rules.list", "yaml"),
    ("https://example.com/download", "mrs"),
])
def test_ini_skips_unmapped_non_native_rule_provider_formats(compile_config, url, source_format):
    provider = {"type": "http", "behavior": "classical", "url": url}
    if source_format:
        provider["format"] = source_format

    output, warnings = compile_config([GOOD], [], ["RULE-SET,Custom,Good", "MATCH,DIRECT"], {"Custom": provider})

    assert "RULE-SET," not in output
    assert "FINAL,DIRECT" in output
    assert any(warning["code"] == "unsupported_rule_sets" and warning["examples"] == [url] for warning in warnings)


@pytest.mark.parametrize("target", ["surge", "shadowrocket-config"])
def test_render_warns_instead_of_exporting_yaml_provider_with_query(monkeypatch, target):
    async def fetch(_url, *, target="mihomo"):
        return """proxies:
- {name: Good, type: ss, server: ss.example.com, port: 443, cipher: aes-128-gcm, password: test}
"""

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    response = TestClient(app).post("/render", json={
        "subscription_url": "https://example.com/sub", "target": target,
        "selected_policy": {
            "mode": "replace", "rules": ["RULE-SET,Custom,Good", "MATCH,DIRECT"],
            "rule_providers": {"Custom": {
                "type": "http", "behavior": "classical", "format": "yaml",
                "url": "https://example.com/rules.yaml?version=1",
            }},
        },
    })

    assert response.status_code == 200, response.text
    assert "RULE-SET," not in response.text
    assert any(warning["code"] == "unsupported_rule_sets"
               for warning in json.loads(response.headers["X-Compile-Warnings"]))


@pytest.mark.parametrize("compile_config", [build_surge_config, build_shadowrocket_config])
@pytest.mark.parametrize("source_format", ["", "text"])
def test_ini_native_rule_provider_keeps_query_and_fragment_exact(compile_config, source_format):
    url = "https://example.com/rules.list?key=synthetic%2Fvalue&v=2&v=1#section"
    providers = {"Custom": {"type": "http", "behavior": "classical", "format": source_format, "url": url}}
    original = deepcopy(providers)

    output, warnings = compile_config([GOOD], [], ["RULE-SET,Custom,Good,no-resolve", "MATCH,DIRECT"], providers)

    assert f"RULE-SET,{url},Good,no-resolve" in output
    assert not warnings
    assert providers == original


_B7_REF = "8818705adee20571a856daf11c9fc69c4929109a"
_B7_SOURCE = f"https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/{_B7_REF}"
_B7_NATIVE = f"https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@{_B7_REF}"
_HENRY_SOURCE = "https://raw.githubusercontent.com/HenryChiao/mihomo_yamls/refs/heads/ruleset/meta"


@pytest.mark.parametrize("compile_config", [build_surge_config, build_shadowrocket_config])
@pytest.mark.parametrize("source_url,source_format,behavior,directive,native_url", [
    (f"{_B7_SOURCE}/rule/Clash/Apple/Apple_Classical_No_Resolve.yaml", "yaml", "classical", "RULE-SET",
     f"{_B7_NATIVE}/rule/Surge/Apple/Apple_All_No_Resolve.list"),
    ("https://ruleset.skk.moe/Clash/domainset/cdn.txt", "text", "domain", "DOMAIN-SET",
     "https://ruleset.skk.moe/List/domainset/cdn.conf"),
    (f"{_HENRY_SOURCE}/domain/ai.mrs", "mrs", "domain", "RULE-SET", f"{_HENRY_SOURCE}/domain/ai.txt"),
])
def test_ini_audited_provider_mapping_keeps_complete_variant_and_url_parameters(
    compile_config, source_url, source_format, behavior, directive, native_url,
):
    parameters = "?v=1&key=synthetic%2Fvalue#section"
    providers = {"Custom": {"type": "http", "behavior": behavior, "format": source_format,
                            "url": source_url + parameters}}
    original = deepcopy(providers)

    output, warnings = compile_config([GOOD], [], ["RULE-SET,Custom,Good,no-resolve", "MATCH,DIRECT"], providers)

    no_resolve = ",no-resolve" if directive == "RULE-SET" else ""
    assert f"{directive},{native_url}{parameters},Good{no_resolve}\n" in output
    assert not warnings
    assert providers == original


@pytest.mark.parametrize("compile_config", [build_surge_config, build_shadowrocket_config])
def test_ini_unknown_classical_variant_does_not_fall_back_to_partial_native_list(compile_config):
    url = f"{_B7_SOURCE}/rule/Clash/Netflix/Netflix_Classical.yaml?version=1"
    providers = {"Custom": {"type": "http", "behavior": "classical", "format": "yaml", "url": url}}

    output, warnings = compile_config([GOOD], [], ["RULE-SET,Custom,Good", "MATCH,DIRECT"], providers)

    assert "Netflix" not in output
    assert any(warning["code"] == "unsupported_rule_sets" for warning in warnings)
