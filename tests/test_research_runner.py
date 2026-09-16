import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

path = Path(__file__).parents[1] / "scripts/run_research.py"
spec = importlib.util.spec_from_file_location("research_runner", path)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_research_input_stays_in_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "ok.yaml").write_text("ok", encoding="utf-8")
    (tmp_path / "outside.yaml").write_text("outside", encoding="utf-8")
    assert runner.input_path(root, "ok.yaml").name == "ok.yaml"
    with pytest.raises(ValueError):
        runner.input_path(root, "../outside.yaml")


def test_research_budgets_are_validated():
    with pytest.raises(ValidationError):
        runner.ResearchRequest(request_id="test", max_api_calls=-1)


def test_classification_fails_before_network_without_secret(monkeypatch):
    monkeypatch.setenv("RESEARCH_MODE", "classify")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        runner.main()
