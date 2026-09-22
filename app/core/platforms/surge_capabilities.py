"""Shared capability declarations for the Surge iOS target."""

SURGE_IOS_RULE_TYPES: frozenset[str] = frozenset(
    {
        "DOMAIN",
        "DOMAIN-SUFFIX",
        "DOMAIN-KEYWORD",
        "IP-CIDR",
        "IP-CIDR6",
        "GEOIP",
        "USER-AGENT",
        "URL-REGEX",
        "DEST-PORT",
        "RULE-SET",
        "MATCH",
        "FINAL",
    }
)
