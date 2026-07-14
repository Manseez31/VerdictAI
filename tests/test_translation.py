"""Unit tests for the translation module. The LLM is faked so these run
offline — they verify prompt construction and input/output guards."""

from types import SimpleNamespace

import pytest

from translation import build_translation_prompt, translate_to_english


class FakeLLM:
    def __init__(self, reply="The council is established. [स्रोत: Nepal Pharmacy Council Act, 2057, धारा 3]"):
        self.reply = reply
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.reply)


NEPALI = "फार्मेसी परिषद्को स्थापना गरिएको छ। [स्रोत: Nepal Pharmacy Council Act, 2057, धारा 3]"


def test_translate_returns_stripped_output():
    llm = FakeLLM(reply="  Hello, translated.  ")
    assert translate_to_english(NEPALI, llm=llm) == "Hello, translated."


def test_prompt_contains_source_text_and_preservation_rules():
    llm = FakeLLM()
    translate_to_english(NEPALI, llm=llm)
    prompt = llm.prompts[0]
    assert NEPALI in prompt                       # translates exactly the given answer
    assert "[स्रोत:" in prompt                    # citation-tag preservation rule
    assert "Section" in prompt                    # section-number handling rule
    assert "ONLY the English translation" in prompt


def test_build_prompt_embeds_text():
    prompt = build_translation_prompt("abc")
    assert "abc" in prompt


def test_empty_input_raises():
    with pytest.raises(ValueError):
        translate_to_english("   ", llm=FakeLLM())


def test_empty_model_output_raises():
    with pytest.raises(ValueError):
        translate_to_english(NEPALI, llm=FakeLLM(reply="   "))


# --- Bidirectional translation (Case Intelligence bilingual toggle) ---

def test_translate_text_to_nepali_prompt():
    from translation import translate_text, build_translation_prompt

    llm = FakeLLM(reply="अनुवादित")
    out = translate_text("Section 3 applies.", target_lang="ne", llm=llm)
    assert out == "अनुवादित"
    assert "Nepali translation:" in llm.prompts[0]  # target language reached the prompt


def test_translate_text_rejects_unknown_lang():
    from translation import translate_text

    with pytest.raises(ValueError):
        translate_text("hi", target_lang="fr", llm=FakeLLM())


def test_translate_to_english_still_default():
    from translation import translate_text

    llm = FakeLLM(reply="English out")
    assert translate_text("नेपाली", llm=llm) == "English out"
    assert "English translation:" in llm.prompts[0]
