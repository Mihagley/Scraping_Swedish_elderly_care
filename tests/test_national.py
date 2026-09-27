from municipal_research.models import Chunk
from municipal_research.national import shard_bounds, shard_items
from municipal_research.triage import triage_chunk


def test_290_is_split_into_12_shards_between_20_and_30():
    items = list(range(290))
    shards = [shard_items(items, 12, i) for i in range(12)]
    assert [len(x) for x in shards] == [25, 25, 24, 24, 24, 24, 24, 24, 24, 24, 24, 24]
    assert sorted(x for shard in shards for x in shard) == items
    assert shard_bounds(290, 12, 0) == (0, 25)
    assert shard_bounds(290, 12, 11) == (266, 290)


def test_triage_preserves_explicit_language_levels():
    chunk = Chunk(
        id="c1",
        document_id="d1",
        start=0,
        end=200,
        text="I äldreomsorgen krävs Svenska 1 eller Svenska som andraspråk 1. GERS B2 används vid rekrytering.",
    )
    result = triage_chunk(chunk)
    assert result["status"] == "relevant"
    assert "svenska_1" in result["hits"]
    assert "svenska_som_andrasprak_1" in result["hits"]
    assert "gers_b2" in result["hits"]


def test_irrelevant_triage_is_not_negative_evidence():
    chunk = Chunk(
        id="c2",
        document_id="d2",
        start=0,
        end=50,
        text="Kommunens parkeringsregler ändrades i juni.",
    )
    result = triage_chunk(chunk)
    assert result["status"] == "irrelevant"
    assert "category" not in result
