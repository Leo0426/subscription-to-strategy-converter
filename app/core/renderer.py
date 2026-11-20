from __future__ import annotations

from io import StringIO

from ruamel.yaml import YAML


_MIN_SHARED_MEMBERS = 8


def _share_long_group_members(config: dict) -> dict:
    groups = config.get("proxy-groups")
    if not isinstance(groups, list):
        return config

    # The YAML emitter uses aliases for repeated list objects. Short menus
    # are easier to read inline even when they contain identical choices.
    shared: dict[tuple[str, ...], list[str]] = {}
    output_groups = groups
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        members = group.get("proxies")
        if not isinstance(members, list) or len(members) < _MIN_SHARED_MEMBERS:
            continue
        if not all(isinstance(member, str) for member in members):
            continue
        canonical = shared.setdefault(tuple(members), members)
        if canonical is not members:
            if output_groups is groups:
                output_groups = groups.copy()
            output_groups[index] = {**group, "proxies": canonical}
    if output_groups is groups:
        return config
    return {**config, "proxy-groups": output_groups}


def render_yaml(config: dict) -> str:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    stream = StringIO()
    yaml.dump(_share_long_group_members(config), stream)
    return stream.getvalue()
