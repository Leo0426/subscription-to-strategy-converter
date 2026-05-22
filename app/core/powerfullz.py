from __future__ import annotations

import httpx
from ruamel.yaml import YAML

from app.models.powerfullz import PowerfullzOptions


class PowerfullzTemplateError(ValueError):
    pass


POWERFULLZ_CDN_BASE = "https://cdn.jsdelivr.net/gh/powerfullz/override-rules@2.3.3/yamls"


def build_powerfullz_yaml_url(options: PowerfullzOptions) -> str:
    filename = (
        f"config_lb-{int(options.loadbalance)}"
        f"_landing-{int(options.landing)}"
        f"_ipv6-{int(options.ipv6)}"
        f"_full-{int(options.full)}"
        f"_keepalive-{int(options.keepalive)}"
        f"_fakeip-{int(options.fakeip)}"
        f"_quic-{int(options.quic)}"
        f"_tun-{int(options.tun)}.yaml"
    )
    return f"{POWERFULLZ_CDN_BASE}/{filename}"


async def load_powerfullz_template(options: PowerfullzOptions) -> dict:
    url = build_powerfullz_yaml_url(options)
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PowerfullzTemplateError(f"failed to load powerfullz template: {url}") from exc

    yaml = YAML(typ="safe")
    try:
        loaded = yaml.load(response.text)
    except Exception as exc:
        raise PowerfullzTemplateError("powerfullz template is not valid YAML") from exc

    if not isinstance(loaded, dict):
        raise PowerfullzTemplateError("powerfullz template must be a YAML object")
    if "proxy-groups" not in loaded or "rules" not in loaded:
        raise PowerfullzTemplateError("powerfullz template is missing proxy-groups or rules")
    return loaded
