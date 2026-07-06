from app.core.policy_catalog import (
    CATEGORY_ORDER,
    classify_provider_name,
    load_policy_catalog,
)


def test_ai_services_are_split_by_vendor():
    assert classify_provider_name("Claude") == ("AI", "Claude")
    assert classify_provider_name("Claude / Domain") == ("AI", "Claude")
    assert classify_provider_name("Claude-zj") == ("AI", "Claude")
    assert classify_provider_name("OpenAI") == ("AI", "OpenAI")
    assert classify_provider_name("openai_domain") == ("AI", "OpenAI")
    assert classify_provider_name("OpenAI_域") == ("AI", "OpenAI")
    assert classify_provider_name("ChatGPT / Domain") == ("AI", "OpenAI")
    assert classify_provider_name("Gemini") == ("AI", "Gemini")
    assert classify_provider_name("AIIP") == ("AI", "AI 通用")


def test_specific_service_wins_over_company_bucket():
    # Apple TV / Apple Music 是流媒体，不该被 Apple 桶吃掉
    assert classify_provider_name("Apple TV") == ("流媒体", "Apple TV")
    assert classify_provider_name("AppleMusic") == ("流媒体", "Apple Music")
    assert classify_provider_name("Apple-CN") == ("其他", "Apple")
    assert classify_provider_name("Xbox") == ("其他", "Xbox")
    assert classify_provider_name("OneDrive") == ("其他", "Microsoft")
    # Meta AI 是 AI，Meta 本体是社交
    assert classify_provider_name("Meta AI / Domain") == ("AI", "Meta AI")
    assert classify_provider_name("Meta") == ("社交通讯", "Meta")


def test_cn_and_ad_buckets_avoid_false_positives():
    # 'add_direct_domain' 里的 "ad" 不是广告
    assert classify_provider_name("add_direct_domain") == ("国内直连", "")
    # 'media!cn_domain' 是非国内媒体，不该进国内直连
    assert classify_provider_name("media!cn_domain") == ("流媒体", "")
    assert classify_provider_name("site_ad") == ("广告拦截", "")
    assert classify_provider_name("BanProgramAD") == ("广告拦截", "")
    # BT tracker 不是隐私 tracking
    assert classify_provider_name("trackerslist") == ("其他", "PT / BT")
    assert classify_provider_name("Tracking") == ("广告拦截", "")


def test_unknown_name_falls_back_to_other():
    assert classify_provider_name("myignore") == ("其他", "")


def test_catalog_providers_all_categorized():
    catalog = load_policy_catalog()
    categories = set(CATEGORY_ORDER)
    for provider in catalog["ruleProviders"]:
        assert provider["category"] in categories
        assert "service" in provider
    facet = catalog["facets"]["providerCategories"]
    assert facet == [c for c in CATEGORY_ORDER if c in {p["category"] for p in catalog["ruleProviders"]}]
