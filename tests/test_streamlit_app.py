"""Browser-script checks, including a real offline demonstration subprocess."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def button(app, label):
    return next(item for item in app.button if item.label == label)


def test_demo_is_separate_from_live_settings():
    app = AppTest.from_file(str(APP)).run()
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
    app = AppTest.from_file(str(APP)).run()
    app.radio[0].set_value("Verklig sökning").run()
    assert not app.exception
    assert len(app.multiselect[0].options) == 290
    button(app, "Välj alla (290)").click().run()
    assert len(app.multiselect[0].value) == 290
    button(app, "Rensa val").click().run()
    assert app.multiselect[0].value == []
    assert button(app, "Starta sökning").disabled
    app.radio[1].set_value("Regioner").run()
    assert len(app.multiselect[0].options) == 21
    app.radio[1].set_value("Både kommuner och regioner").run()
    assert len(app.multiselect[0].options) == 311
    assert not app.exception
