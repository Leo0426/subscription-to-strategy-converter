from app.core.powerfullz import build_powerfullz_yaml_url
from app.models.powerfullz import PowerfullzOptions


def test_build_powerfullz_yaml_url() -> None:
    url = build_powerfullz_yaml_url(
        PowerfullzOptions(
            loadbalance=True,
            landing=True,
            ipv6=False,
            full=True,
            keepalive=False,
            fakeip=True,
            quic=True,
        )
    )

    assert url == (
        "https://cdn.jsdelivr.net/gh/powerfullz/override-rules@2.3.3/yamls/"
        "config_lb-1_landing-1_ipv6-0_full-1_keepalive-0_fakeip-1_quic-1_tun-0.yaml"
    )
