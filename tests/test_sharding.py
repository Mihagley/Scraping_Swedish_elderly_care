import json

import pytest

from municipal_research.config import Municipality
from municipal_research.sharding import build_shard_plan, verify_run_hashes
from municipal_research.storage import digest


def municipalities(count: int) -> list[Municipality]:
    return [
        Municipality(
            id=f"{index:04d}",
            name=f"Municipality {index}",
            domains=[f"municipality-{index}.example.se"],
            seeds=[],
        )
        for index in range(count)
    ]


def test_290_registry_is_balanced_into_20_to_30_sized_shards():
    items = municipalities(290)
    plan = build_shard_plan(items, expected=290, target_size=25)
    assert len(plan["shards"]) == 12
    assert {shard["size"] for shard in plan["shards"]} == {24, 25}
    flattened = [item for shard in plan["shards"] for item in shard["municipality_ids"]]
    assert flattened == [municipality.id for municipality in items]
    assert len(flattened) == len(set(flattened)) == 290


def test_registry_validation_rejects_wrong_count():
    with pytest.raises(ValueError, match="Expected 290 municipalities"):
        build_shard_plan(municipalities(289), expected=290)


def test_verify_run_hashes_detects_tampering(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    artifact = run / "results.json"
    artifact.write_text('{"ok": true}\n', encoding="utf-8")
    manifest = {"artifacts": {"results.json": digest(artifact.read_bytes())}}
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_run_hashes(run) == 1
    artifact.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing or modified artifacts"):
        verify_run_hashes(run)
