from copy import deepcopy
from io import StringIO

from ruamel.yaml import YAML

from app.core.renderer import render_yaml


def test_repeated_long_proxy_group_members_use_yaml_aliases_without_mutating_input():
    members = [f"测试节点 {number:03d}" for number in range(144)]
    groups = [
        {"name": "自动选择", "type": "url-test", "proxies": members},
        {"name": "手动选择", "type": "select", "proxies": members.copy()},
        {"name": "第三组选项", "type": "select", "proxies": members.copy()},
    ]
    config = {"proxy-groups": groups, "other-field": members.copy()}
    original = deepcopy(config)
    original_members = [group["proxies"] for group in groups]

    rendered = render_yaml(config)
    recovered = YAML(typ="safe").load(rendered)
    recovered_members = [group["proxies"] for group in recovered["proxy-groups"]]
    assert recovered == original
    assert recovered_members[0] is recovered_members[1] is recovered_members[2]
    assert recovered["other-field"] is not recovered_members[0]
    assert config == original
    assert all(group["proxies"] is before for group, before in zip(groups, original_members))

    baseline = StringIO()
    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.dump(original, baseline)
    assert len(rendered.encode()) < 0.6 * len(baseline.getvalue().encode())


def test_distinct_short_and_nonstandard_members_are_preserved():
    members = [f"Node {number}" for number in range(12)]
    irregular = [*members, {"native": True}]
    config = {
        "proxy-groups": [
            {"name": "原始", "proxies": members},
            {"name": "不同顺序", "proxies": list(reversed(members))},
            {"name": "部分相同", "proxies": members[:-1]},
            {"name": "短列表一", "proxies": ["DIRECT", "REJECT"]},
            {"name": "短列表二", "proxies": ["DIRECT", "REJECT"]},
            {"name": "原生字段一", "proxies": irregular},
            {"name": "原生字段二", "proxies": deepcopy(irregular)},
            {"name": "其他格式", "proxies": "DIRECT"},
            {"name": "无节点列表"},
            "opaque",
        ]
    }

    recovered = YAML(typ="safe").load(render_yaml(config))
    assert recovered == config
    groups = recovered["proxy-groups"]
    assert groups[3]["proxies"] is not groups[4]["proxies"]
    assert groups[5]["proxies"] is not groups[6]["proxies"]
    native = {"proxy-groups": {"native": "value"}}
    assert YAML(typ="safe").load(render_yaml(native)) == native
