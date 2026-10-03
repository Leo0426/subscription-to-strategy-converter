"""INI AI RuleProvider replacement preserves first-match routing."""

from copy import deepcopy

import pytest

from app.core.parsers.clash import clash_to_ir
from app.core.platforms.shadowrocket import build_shadowrocket_config
from app.core.platforms.surge import build_surge_config, substitute_ai_provider_rules
from app.core.policy_simulator import simulate_destination
from app.core.policy_workspace import config_to_workspace


AI_PROVIDERS = {"ai": {
    "type": "http", "behavior": "domain", "format": "text",
    "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/abc/geo/geosite/category-ai-!cn.list",
}}
NODE = clash_to_ir({
    "name": "US", "type": "ss", "server": "us.example.com", "port": 443,
    "cipher": "aes-128-gcm", "password": "test",
})


@pytest.fixture(params=[build_surge_config, build_shadowrocket_config], ids=["surge", "shadowrocket"])
def compile_rules(request):
    def compile_rules(rules):
        output, warnings = request.param([NODE], [], rules, AI_PROVIDERS)
        assert not warnings
        return output.split("[Rule]\n", 1)[1].strip().splitlines()

    return compile_rules


def test_ai_replacement_precedes_google_even_with_same_domain_rule_later(compile_rules):
    rules = compile_rules([
        "RULE-SET,ai,US",
        "DOMAIN-SUFFIX,google.com,DIRECT",
        "DOMAIN-SUFFIX,gemini.google.com,US",
        "MATCH,DIRECT",
    ])

    trace = simulate_destination(config_to_workspace({"rules": rules}, [NODE]), "gemini.google.com")
    assert trace.target == "US"
    assert rules.index("DOMAIN-SUFFIX,gemini.google.com,US") < rules.index("DOMAIN-SUFFIX,google.com,DIRECT")
    assert rules.count("DOMAIN-SUFFIX,gemini.google.com,US") == 1


@pytest.mark.parametrize("explicit_first", [True, False], ids=["earlier-override", "later-override"])
def test_ai_replacement_keeps_different_target_rules_in_their_order(compile_rules, explicit_first):
    explicit = "DOMAIN-SUFFIX,gemini.google.com,REJECT"
    source = ["RULE-SET,ai,US", "DOMAIN-SUFFIX,google.com,DIRECT", "MATCH,DIRECT"]
    source.insert(0 if explicit_first else 2, explicit)

    rules = compile_rules(source)

    assert explicit in rules
    trace = simulate_destination(config_to_workspace({"rules": rules}, [NODE]), "gemini.google.com")
    assert trace.target == ("REJECT" if explicit_first else "US")
    if explicit_first:
        assert "DOMAIN-SUFFIX,gemini.google.com,US" not in rules
    else:
        assert rules.index("DOMAIN-SUFFIX,gemini.google.com,US") < rules.index(explicit)


def test_ai_replacement_is_idempotent_and_keeps_shared_cdn_hosts_exact(compile_rules):
    source = [
        "RULE-SET,ai,US",
        "DOMAIN-SUFFIX,google.com,DIRECT",
        "DOMAIN-SUFFIX,gemini.google.com,US",
        "MATCH,DIRECT",
    ]
    rules = compile_rules(source)

    assert compile_rules(rules) == rules
    workspace = config_to_workspace({"rules": rules}, [NODE])
    for hostname in ("cdn.workos.com", "cdn.openaimerge.com", "workos.imgix.net"):
        assert simulate_destination(workspace, hostname).target == "US"
        assert simulate_destination(workspace, f"other.{hostname}").target == "DIRECT"
    for hostname in ("other.workos.com", "other.openaimerge.com", "other.imgix.net"):
        assert simulate_destination(workspace, hostname).target == "DIRECT"


def test_ai_replacement_only_deduplicates_inserted_rules_and_preserves_inputs():
    rules = [
        "DOMAIN,other.example,DIRECT",
        "DOMAIN,other.example,DIRECT",
        "RULE-SET,ai,US",
        "DOMAIN-SUFFIX,google.com,DIRECT",
        "domain-suffix, Gemini.Google.COM , US",
        "DOMAIN-SUFFIX,gemini.google.com,REJECT",
        "MATCH,DIRECT",
    ]
    providers = deepcopy(AI_PROVIDERS)
    original = deepcopy((rules, providers))

    replaced = substitute_ai_provider_rules(rules, providers)

    assert (rules, providers) == original
    assert replaced.count("DOMAIN,other.example,DIRECT") == 2
    assert replaced.count("DOMAIN-SUFFIX,gemini.google.com,US") == 1
    assert "domain-suffix, Gemini.Google.COM , US" not in replaced
    assert "DOMAIN-SUFFIX,gemini.google.com,REJECT" in replaced
    assert substitute_ai_provider_rules(replaced, providers) == replaced
