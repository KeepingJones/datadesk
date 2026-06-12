"""Post classifier — v3 taxonomy incl. the adversarial cases from research-handoff-v3."""

from datadesk.ai.post_classifier import classify_post


def test_v3_adversarial_nomination_is_noise():
    c = classify_post("I am pleased to announce the Nomination of very successful businessman...")
    assert c.impact_class == "NOISE"
    assert c.actionable_tickers == []


def test_v3_adversarial_deere_tariff_threat():
    c = classify_post(
        "If John Deere moves their factories to Mexico, we will put a 200% tariff "
        "on everything they sell!"
    )
    assert c.impact_class == "TARIFF_THREAT"
    assert c.actionable_tickers == ["DE"]
    assert c.sentiment == "NEGATIVE"


def test_company_grievance():
    c = classify_post("Disney is a disgrace to this Country. Terrible company!")
    assert c.impact_class == "COMPANY_GRIEVANCE"
    assert c.actionable_tickers == ["DIS"]
    assert c.sentiment == "NEGATIVE"


def test_company_endorsement():
    c = classify_post("Tesla is a great company and Elon is doing a great job!")
    assert c.impact_class == "COMPANY_ENDORSEMENT"
    assert "TSLA" in c.actionable_tickers
    assert c.sentiment == "POSITIVE"


def test_macro_commentary_has_no_single_stock_tickers():
    c = classify_post("The Federal Reserve and Jay Powell must lower interest rates NOW!")
    assert c.impact_class == "MACRO_COMMENTARY"
    assert c.actionable_tickers == []


def test_company_mention_without_signal_words_is_noise():
    c = classify_post("Met with the CEO of Apple today. Good meeting.")
    assert c.impact_class == "NOISE"


def test_empty_post_is_noise():
    assert classify_post("").impact_class == "NOISE"
