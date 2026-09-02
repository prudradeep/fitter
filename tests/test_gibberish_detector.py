import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.gibberish_detector import check_gibberish, validate_text_meaning


@pytest.mark.parametrize(
    "text",
    [
        "",
        "     ",
        "asdfghjkl",
        "qwertyuiop",
        "zxcvbnm",
        "asdf qwer zxcv",
        "aaaaaaa bbbbbbbb",
        "@#$%^&*",
        "dfkjgh dfgkjh sdfkjgh",
        "xjskq plmzx qowei",
        "sldfkj falsjdf laskj fda",
    ],
)
def test_obvious_gibberish_is_rejected(text):
    assert check_gibberish(text).classification == "GIBBERISH"


@pytest.mark.parametrize(
    "text",
    [
        "Heating and cooling costs increase",
        "Electricity bills become unaffordable",
        "Low income households may struggle to afford heat pumps",
        "I like sunny weather",
        "Regional employment shock and left-behind energy regions",
        "Reinforcement of gender inequality through androcentric climate policies",
        "Exposure to poor quality solar panel installations and bad commercial practices",
        "Baden-Württemberg",
        "Photovoltaic systems",
        "NUTS2",
        "CO2 emissions",
        "PM2.5 exposure",
        "EU ETS",
        "heat-pump installation",
    ],
)
def test_technical_and_unrelated_natural_language_is_meaningful(text):
    assert check_gibberish(text).classification == "LIKELY_MEANINGFUL"


@pytest.mark.parametrize(
    "text",
    [
        "heating qwer costs asdf increase",
        "solar panels xjksdf installation",
        "energy transition blahblah qwert",
    ],
)
def test_mixed_inputs_remain_uncertain(text):
    assert check_gibberish(text).classification == "UNCERTAIN"


def test_llm_fallback_is_only_used_for_uncertain_text():
    with patch(
        "app.services.gibberish_detector.ask_llm_chat",
        new=AsyncMock(
            return_value='{"classification":"meaningful","confidence":0.9,"reason":"Interpretable text."}'
        ),
    ) as llm:
        result = asyncio.run(validate_text_meaning("heating qwer costs asdf increase"))
        assert result.classification == "LIKELY_MEANINGFUL"
        llm.assert_awaited_once()


def test_obvious_gibberish_does_not_call_llm():
    with patch("app.services.gibberish_detector.ask_llm_chat", new=AsyncMock()) as llm:
        result = asyncio.run(validate_text_meaning("asdfgh qwerty zxcv"))
        assert result.classification == "GIBBERISH"
        llm.assert_not_awaited()
