import json
import re

from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.parsers.clash import ir_to_clash_dict
from app.core.template_engine import (
    LEO_TEMPLATE_ID,
    apply_template,
    filter_auto_test_protocols,
    load_template,
)
from app.ir import ProxyNode
from app.main import app
from app.models.request import ConvertRequest


def node(name: str, protocol: str) -> ProxyNode:
    return ProxyNode(
        name=name,
        protocol=protocol,
        server=f"{name.replace(' ', '-').lower()}.example.com",
        port=443,
        extra={"password": "test"} if protocol == "anytls" else {},
    )


def group(config: dict, name: str) -> dict:
    return next(item for item in config["proxy-groups"] if item["name"] == name)


def mixed_nodes() -> list[ProxyNode]:
    nodes = [node(f"香港 SS {i:02}", "ss") for i in range(1, 16)]
    nodes += [node(f"其他 SS {i:02}", "ss") for i in range(1, 69)]
    nodes += [node(f"香港 AnyTLS {i:02}", "anytls") for i in range(1, 17)]
    nodes += [node(f"美国 AnyTLS {i:02}", "anytls") for i in range(1, 46)]
    assert len(nodes) == 144
    return nodes


def test_surge_protocol_preferences_are_normalized_and_deduplicated() -> None:
    request = ConvertRequest.model_validate(
        {
            "subscription_url": "https://example.com/sub",
            "surge_preferences": {
                "auto_test_protocols": [" AnyTLS ", "anytls", "SS"]
            },
        }
    )

    assert request.surge_preferences.auto_test_protocols == ["anytls", "ss"]


def test_legacy_request_defaults_to_no_surge_filter() -> None:
    request = ConvertRequest(subscription_url="https://example.com/sub")

    assert request.surge_preferences.auto_test_protocols == []


def test_anytls_filter_reduces_automatic_groups_but_keeps_manual_nodes() -> None:
    nodes = mixed_nodes()
    config = apply_template(load_template(LEO_TEMPLATE_ID), nodes)

    diagnostics = filter_auto_test_protocols(config, nodes, ["anytls"])

    assert len(group(config, "自动选择")["proxies"]) == 61
    assert len(group(config, "香港自动")["proxies"]) == 16
    assert len(group(config, "手动选择")["proxies"]) == 144
    assert diagnostics == [
        {
            "code": "auto_test_protocol_filter",
            "group": "自动选择",
            "protocols": ["anytls"],
            "before": 144,
            "after": 61,
        },
        {
            "code": "auto_test_protocol_filter",
            "group": "香港自动",
            "protocols": ["anytls"],
            "before": 31,
            "after": 16,
        },
    ]


def test_empty_protocol_filter_preserves_legacy_membership() -> None:
    nodes = mixed_nodes()
    config = apply_template(load_template(LEO_TEMPLATE_ID), nodes)

    diagnostics = filter_auto_test_protocols(config, nodes, [])

    assert len(group(config, "自动选择")["proxies"]) == 144
    assert len(group(config, "香港自动")["proxies"]) == 31
    assert diagnostics == []


def test_filter_keeps_nested_groups_and_filters_only_node_members() -> None:
    config = {
        "proxy-groups": [
            {"name": "Nested", "type": "select", "proxies": ["SS"]},
            {
                "name": "Auto",
                "type": "url-test",
                "proxies": ["SS", "TLS", "Nested"],
            },
        ],
        "rules": ["MATCH,Auto"],
    }

    diagnostics = filter_auto_test_protocols(
        config,
        [node("SS", "ss"), node("TLS", "anytls")],
        ["anytls"],
    )

    assert group(config, "Auto")["proxies"] == ["TLS", "Nested"]
    assert diagnostics[0]["before"] == 2
    assert diagnostics[0]["after"] == 1


def test_empty_filtered_group_is_closed_without_direct_synthesis() -> None:
    config = {
        "proxy-groups": [
            {"name": "Auto", "type": "url-test", "proxies": ["SS"]},
            {"name": "Default", "type": "select", "proxies": ["Auto", "SS"]},
        ],
        "rules": ["MATCH,Default"],
    }

    filter_auto_test_protocols(config, [node("SS", "ss")], ["anytls"])

    assert "Auto" not in {item["name"] for item in config["proxy-groups"]}
    assert group(config, "Default")["proxies"] == ["SS"]
    assert all(
        "DIRECT" not in item.get("proxies", [])
        for item in config["proxy-groups"]
    )


def _surge_group_members(conf: str, name: str) -> list[str]:
    line = next(item for item in conf.splitlines() if item.startswith(f"{name} ="))
    fields = [field.strip() for field in line.split("=", 1)[1].split(",")]
    return [field for field in fields[1:] if "=" not in field]


def test_render_applies_protocol_filter_only_to_surge_target(monkeypatch) -> None:
    nodes = mixed_nodes()

    async def fake_load_subscription(url: str, *, target: str):
        return nodes, {"proxies": [ir_to_clash_dict(item) for item in nodes]}

    monkeypatch.setattr("app.api.convert.load_subscription", fake_load_subscription)
    client = TestClient(app)
    payload = {
        "subscription_url": "https://example.com/sub",
        "publication_targets": ["mihomo", "surge"],
        "target": "mihomo",
        "surge_preferences": {"auto_test_protocols": ["anytls"]},
    }

    mihomo = client.post("/render", json=payload)
    surge = client.post("/render", json={**payload, "target": "surge"})

    assert mihomo.status_code == 200, mihomo.text
    assert surge.status_code == 200, surge.text
    mihomo_config = YAML(typ="safe").load(mihomo.text)
    assert len(group(mihomo_config, "自动选择")["proxies"]) == 144
    assert len(_surge_group_members(surge.text, "自动选择")) == 61
    assert len(_surge_group_members(surge.text, "香港自动")) == 16
    assert len(_surge_group_members(surge.text, "手动选择")) == 144


def test_multi_target_check_reports_filter_diagnostics_only_for_surge(monkeypatch) -> None:
    nodes = mixed_nodes()

    async def fake_load_subscription(url: str, *, target: str):
        return nodes, {"proxies": [ir_to_clash_dict(item) for item in nodes]}

    monkeypatch.setattr("app.api.convert.load_subscription", fake_load_subscription)
    client = TestClient(app)
    response = client.post(
        "/check",
        json={
            "subscription_url": "https://example.com/sub",
            "publication_targets": ["mihomo", "surge"],
            "target": "mihomo",
            "surge_preferences": {"auto_test_protocols": ["anytls"]},
        },
    )

    assert response.status_code == 200, response.text
    clients = {item["target"]: item for item in response.json()["clients"]}
    assert not any(
        warning["code"] == "auto_test_protocol_filter"
        for warning in clients["mihomo"]["warnings"]
    )
    filter_warnings = [
        warning
        for warning in clients["surge"]["warnings"]
        if warning["code"] == "auto_test_protocol_filter"
    ]
    assert [(item["group"], item["before"], item["after"]) for item in filter_warnings] == [
        ("自动选择", 144, 61),
        ("香港自动", 31, 16),
    ]


def test_surge_workspace_preview_exposes_policy_diagnostics(monkeypatch) -> None:
    nodes = mixed_nodes()

    async def fake_load_subscription(url: str, *, target: str):
        return nodes, {"proxies": [ir_to_clash_dict(item) for item in nodes]}

    monkeypatch.setattr("app.api.convert.load_subscription", fake_load_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "target": "surge",
            "surge_preferences": {"auto_test_protocols": ["anytls"]},
        },
    )

    assert response.status_code == 200, response.text
    warnings = response.json()["workspace"]["compile_warnings"]
    assert [item["code"] for item in warnings] == [
        "auto_test_protocol_filter",
        "auto_test_protocol_filter",
    ]


def _section(config: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^\[{re.escape(name)}\]\n(.*?)(?=^\[|\Z)",
        config,
    )
    assert match is not None
    return match.group(1).strip()


def _native_source() -> str:
    proxies = [
        f"香港 SS {index:02} = ss, hk-ss-{index}.example.com, 443, "
        "encrypt-method=aes-128-gcm, password=test"
        for index in range(1, 16)
    ]
    proxies += [
        f"其他 SS {index:02} = ss, other-ss-{index}.example.com, 443, "
        "encrypt-method=aes-128-gcm, password=test"
        for index in range(1, 69)
    ]
    proxies += [
        f"香港 AnyTLS {index:02} = anytls, hk-any-{index}.example.com, 443, "
        "password=test, skip-cert-verify=true"
        for index in range(1, 17)
    ]
    proxies += [
        f"美国 AnyTLS {index:02} = anytls, us-any-{index}.example.com, 443, "
        "password=test, skip-cert-verify=true"
        for index in range(1, 46)
    ]
    return """[General]
allow-wifi-access = true
doh-server = https://resolver.example/dns-query?token=private-token
loglevel = info
include-all-networks = true
include-apns = true
include-cellular-services = true

[Proxy]
""" + "\n".join(proxies) + "\n"


def test_end_to_end_saved_surge_profile_filters_and_audits(
    tmp_path,
    monkeypatch,
) -> None:
    async def fetch(_url: str) -> str:
        return _native_source()

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    with TestClient(app) as client:
        created_response = client.post(
            "/profiles",
            json={
                "subscription_url": "https://example.com/sub",
                "target": "surge",
                "publication_targets": ["surge"],
                "surge_preferences": {"auto_test_protocols": ["anytls"]},
            },
        )
        assert created_response.status_code == 201, created_response.text
        response = client.get(created_response.json()["subscribe_urls"]["surge"])

    assert response.status_code == 200, response.text
    assert len(_surge_group_members(response.text, "自动选择")) == 61
    assert len(_surge_group_members(response.text, "香港自动")) == 16
    assert len(_surge_group_members(response.text, "手动选择")) == 144
    assert "PROCESS-NAME" not in _section(response.text, "Rule")
    assert _section(response.text, "Rule").splitlines()[-1].startswith("FINAL,")
    assert "allow-wifi-access = true" in _section(response.text, "General")
    warnings = json.loads(response.headers.get("X-Compile-Warnings", "[]"))
    assert {item["code"] for item in warnings} >= {
        "auto_test_protocol_filter",
        "unsupported_rule_types",
        "wifi_proxy_access_without_auth",
        "insecure_tls_nodes",
    }
    assert "private-token" not in json.dumps(warnings)
