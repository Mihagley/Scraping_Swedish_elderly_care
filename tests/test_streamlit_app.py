"""Browser-script checks, including a real offline demonstration subprocess."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def button(app, label):
    return next(item for item in app.button if item.label == label)


def test_demo_is_separate_from_live_settings():
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    assert not app.exception
    assert app.title[0].value == "Sök i offentliga källor"
    assert not app.multiselect
    assert not app.text_input
    button(app, "Starta demonstration").click().run()
    process = app.session_state["process"]
    try:
        assert process.wait(timeout=30) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    app.run()
    assert not app.exception
    assert any("påhittade" in item.value for item in app.warning)
    assert len(app.get("download_button")) == 2


def test_national_selection_and_region_scope():
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    app.radio[0].set_value("Verklig sökning").run()
    assert not app.exception
    assert len(app.multiselect[0].options) == 290
    app.radio[2].set_value("Alla i listan").run()
    assert not app.multiselect
    assert any("Alla 290 i listan" in item.value for item in app.success)
    app.radio[1].set_value("Regioner").run()
    assert not app.multiselect
    assert any("Alla 21 i listan" in item.value for item in app.success)
    app.radio[1].set_value("Både kommuner och regioner").run()
    assert any("Alla 311 i listan" in item.value for item in app.success)
    app.radio[2].set_value("Välj enskilda").run()
    assert len(app.multiselect[0].options) == 311
    button(app, "Rensa val").click().run()
    assert app.multiselect[0].value == []
    assert button(app, "Starta sökning").disabled
    assert not app.exception


def test_local_model_defaults_to_crawl_without_openai_key():
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    app.radio[0].set_value("Verklig sökning").run()
    ai = next(item for item in app.selectbox if item.label == "AI för klassificering")
    ai.set_value("ollama").run()
    assert not app.exception
    discovery = next(item for item in app.selectbox if item.label == "Sökmetod")
    assert discovery.value == "crawl"
    assert not any(item.label == "Din API-nyckel för OpenAI" for item in app.text_input)
    assert any(item.label == "Lokal modell (inklusive tagg)" for item in app.text_input)
    discovery.set_value("hybrid").run()
    assert any(item.label == "Din API-nyckel för OpenAI" for item in app.text_input)
    assert any("skickar sökfrågor till OpenAI" in item.value for item in app.warning)
    assert not app.exception
