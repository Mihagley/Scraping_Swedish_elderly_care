from pathlib import Path

from municipal_research import desktop
from municipal_research.interface import parse_locations


def test_no_key_forces_crawl():
    assert desktop.effective_discovery("  ", "hybrid") == "crawl"
    assert desktop.effective_discovery("key", "hybrid") == "hybrid"


def test_worker_arguments_for_source_and_frozen(monkeypatch):
    arguments = ["run", "--run-dir", "folder with spaces"]
    command = desktop.worker_command(arguments, Path("worker.log"))
    assert command[1:3] == ["-m", "municipal_research.desktop"]
    assert command[-3:] == arguments
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    command = desktop.worker_command(arguments, Path("worker.log"))
    assert "-m" not in command
    assert command[-3:] == arguments
    assert "--worker-log" in command


def test_registry_roundtrip_preserves_codes_and_regions(tmp_path):
    locations = parse_locations(
        "id,name,kind,domains,seeds\n0123,Å kommun,kommun,example.se,https://example.se/\nregion-01,En region,region,region.se,\n"
    )
    path = tmp_path / "locations.csv"
    desktop.write_locations(path, locations)
    assert parse_locations(path.read_text(encoding="utf-8")) == locations


def test_data_directory_uses_writable_user_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = desktop.data_directory()
    assert path == tmp_path / "Kommunutredaren"
    assert path.is_dir()
