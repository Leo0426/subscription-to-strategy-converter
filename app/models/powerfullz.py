from pydantic import BaseModel


class PowerfullzOptions(BaseModel):
    loadbalance: bool = False
    landing: bool = False
    ipv6: bool = False
    full: bool = True
    keepalive: bool = False
    fakeip: bool = True
    quic: bool = False
    tun: bool = False
