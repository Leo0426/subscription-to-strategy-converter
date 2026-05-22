from __future__ import annotations

from ruamel.yaml import YAML


class ParseError(ValueError):
    pass


REQUIRED_NODE_FIELDS = ("name", "type", "server", "port")


def parse_clash_yaml_full(content: str) -> tuple[list[dict], dict]:
    """Return (proxies, raw_dict). raw_dict is the full parsed YAML including proxy-groups and rules."""
    yaml = YAML(typ="safe")
    try:
        data = yaml.load(content)
    except Exception as exc:
        raise ParseError("subscription content is not valid YAML") from exc

    if not isinstance(data, dict):
        raise ParseError("subscription YAML must be an object")

    proxies = data.get("proxies")
    if proxies is None:
        raise ParseError("subscription YAML does not contain proxies")
    if not isinstance(proxies, list):
        raise ParseError("proxies must be a list")
    if not proxies:
        raise ParseError("proxies list is empty")

    parsed: list[dict] = []
    for index, proxy in enumerate(proxies, start=1):
        if not isinstance(proxy, dict):
            raise ParseError(f"proxy #{index} must be an object")
        missing = [field for field in REQUIRED_NODE_FIELDS if field not in proxy or proxy[field] in (None, "")]
        if missing:
            raise ParseError(f"proxy #{index} is missing required fields: {', '.join(missing)}")
        parsed.append(dict(proxy))

    return parsed, dict(data)


def parse_clash_yaml(content: str) -> list[dict]:
    proxies, _ = parse_clash_yaml_full(content)
    return proxies
