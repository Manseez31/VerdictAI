from arena_routing import (
    build_where_for_category,
    choose_where,
    decide_arena,
    is_generic_greeting,
)


def test_ui_selection_overrides_everything():
    assert decide_arena("pharmacy question", "Sports Act") == "Sports Act"


def test_greeting_returns_all_auto():
    assert decide_arena("hello", "All (auto)") == "All (auto)"
    assert decide_arena("Namaste", "All (auto)") == "All (auto)"


def test_empty_message_returns_all_auto():
    assert decide_arena("   ", "All (auto)") == "All (auto)"


def test_keyword_rules_route_before_classifier():
    classify_calls = []
    assert (
        decide_arena("pharmacy license process", "All (auto)", classify_fn=classify_calls.append)
        == "Pharmacy Act"
    )
    assert classify_calls == []  # classifier must not be consulted


def test_falls_back_to_classifier_when_no_keyword_matches():
    assert (
        decide_arena("something unrelated to any act", "All (auto)", classify_fn=lambda q: "Sports Act")
        == "Sports Act"
    )


def test_falls_back_to_all_auto_without_classifier():
    assert decide_arena("something unrelated to any act", "All (auto)") == "All (auto)"


def test_is_generic_greeting():
    assert is_generic_greeting("hi")
    assert is_generic_greeting("Thank You")
    assert not is_generic_greeting("hi, what does the pharmacy act say")


def test_build_where_for_category():
    assert build_where_for_category("All (auto)") is None
    assert build_where_for_category("Pharmacy Act") == {"source_file": "pharmacy.pdf"}


def test_choose_where_matches_keywords():
    assert choose_where("khop lagauna paincha?", "") == {"source_file": "immunization.pdf"}
    assert choose_where("no matching keywords here", "") is None
