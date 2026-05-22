"""Unified Intermediate Representation for proxy nodes.

All input parsers (Clash YAML, URI schemes) produce ProxyNode objects.
All output renderers (Mihomo, Sing-box) consume ProxyNode objects.
The normalizer and strategy system operate on ProxyNode lists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TLSConfig:
    enabled: bool = False
    sni: str = ""
    insecure: bool = False  # skip-cert-verify
    alpn: list[str] = field(default_factory=list)
    fingerprint: str = ""   # uTLS fingerprint
    reality: dict[str, Any] = field(default_factory=dict)  # {public_key, short_id}


@dataclass
class TransportConfig:
    type: str = ""            # "ws" | "grpc" | "h2" | ""
    path: str = ""
    host: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    service_name: str = ""    # gRPC service name


@dataclass
class ProxyNode:
    """Protocol-agnostic proxy node IR.

    ``extra`` holds all protocol-specific parameters that don't fit the
    common fields above (e.g. cipher/password for SS, uuid/alter_id for
    VMess, obfs settings for Hysteria2).
    """
    name: str
    protocol: str   # ss | vmess | vless | trojan | hysteria2 | tuic | socks5 | http
    server: str
    port: int
    tls: TLSConfig = field(default_factory=TLSConfig)
    transport: TransportConfig = field(default_factory=TransportConfig)
    extra: dict[str, Any] = field(default_factory=dict)


BUILTIN_POLICY_TARGETS = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "GLOBAL"}


@dataclass
class RuleProvider:
    name: str
    type: str = ""
    behavior: str = ""
    format: str = ""
    url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyRule:
    id: str
    index: int
    type: str
    match: str = ""
    target: str = ""
    provider: str = ""
    options: list[str] = field(default_factory=list)
    raw: Any = None


@dataclass
class ProxyGroup:
    name: str
    type: str = "select"
    members: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyWorkspace:
    target: str
    proxies: list[ProxyNode] = field(default_factory=list)
    proxy_groups: list[ProxyGroup] = field(default_factory=list)
    rules: list[PolicyRule] = field(default_factory=list)
    rule_providers: list[RuleProvider] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyGraphNode:
    id: str
    type: str
    label: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyGraphEdge:
    id: str
    source: str
    target: str
    type: str
    label: str = ""


@dataclass
class PolicyGraph:
    nodes: list[PolicyGraphNode] = field(default_factory=list)
    edges: list[PolicyGraphEdge] = field(default_factory=list)


@dataclass
class AnalyzerFinding:
    severity: str
    code: str
    message: str
    path: str
    ref: str = ""


@dataclass
class SimulationStep:
    type: str
    ref: str
    message: str
    matched: bool | None = None


@dataclass
class SimulationTrace:
    destination: str
    matched_rule: PolicyRule | None = None
    target: str = ""
    resolved: str = ""
    steps: list[SimulationStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
