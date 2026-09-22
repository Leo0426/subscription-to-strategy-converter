from app.core.template_engine import (
    LEO_TEMPLATE_ID,
    apply_template,
    filter_auto_test_protocols,
    load_template,
)
from app.ir import ProxyNode
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
