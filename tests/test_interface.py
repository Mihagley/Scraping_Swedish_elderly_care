import csv
import runpy
import unittest
from pathlib import Path

from municipal_research.config import load_config, load_municipalities
from municipal_research.interface import configure, parse_locations, requires_openai

ROOT = Path(__file__).resolve().parents[1]


class InterfaceTests(unittest.TestCase):
    def test_registries_and_legacy_csv(self):
        kommun = load_municipalities(ROOT / "examples/municipalities.csv")
        regions = load_municipalities(ROOT / "examples/regions.csv")
        self.assertEqual(len(regions), 21)
        self.assertEqual(len(kommun), 290)
        self.assertEqual(next(x.id for x in kommun if x.name == "Stockholm"), "0180")
        self.assertTrue(all(x.kind == "kommun" for x in kommun))
        self.assertTrue(all(x.kind == "region" for x in regions))
        self.assertEqual(len({x.id for x in kommun + regions}), 311)

    def test_registry_matches_source_snapshots(self):
        builder = runpy.run_path(str(ROOT / "scripts/build_municipality_registry.py"))
        expected = builder["build_rows"]()
        with (ROOT / "examples/municipalities.csv").open(encoding="utf-8", newline="") as handle:
            self.assertEqual(list(csv.DictReader(handle)), expected)
        self.assertTrue(all(len(row["id"]) == 4 and row["id"].isdigit() for row in expected))
        names = {row["name"] for row in expected}
        self.assertTrue({"Göteborg", "Malmö", "Gotland", "Malung-Sälen", "Övertorneå"} <= names)
        pilot = load_municipalities(ROOT / "examples/municipalities_pilot.csv")
        self.assertEqual(len(pilot), 6)
        by_id = {row["id"]: row for row in expected}
        self.assertTrue(all(by_id[row.id]["name"] == row.name for row in pilot))

    def test_upload_rejects_duplicate_ids_and_invalid_kind(self):
        for text in [
            "id,name,domains\nx,A,a.se\nx,B,b.se",
            "id,name,domains,kind\nx,A,a.se,county",
            "name,domains\nA,a.se",
            "id,name,domains\nx,A",
            "id,name,domains\nx,A,a.se,extra",
        ]:
            with self.assertRaises(ValueError):
                parse_locations(text)

    def test_settings_are_validated_without_mutating_codebook(self):
        base = load_config(ROOT / "examples/interface.yaml")
        config = configure(
            base, "New question?", "språkkrav, B2", "{location} {question}", "crawl", 2, 10
        )
        self.assertEqual(config.discovery.keywords, ["språkkrav", "B2"])
        self.assertEqual(config.research.labels, base.research.labels)
        self.assertNotEqual(config.research.question, base.research.question)
        for query in ["{location.name}", "{bad}", "{location!r}", "{"]:
            with self.assertRaises(ValueError):
                configure(base, "Question", "", query, "crawl", 2, 10)
        with self.assertRaises(ValueError):
            configure(base, "Question", "", "", "crawl", 0, 10)

    def test_local_settings_cover_all_classification_roles(self):
        base = load_config(ROOT / "examples/interface.yaml")
        config = configure(
            base, "Fråga?", "språk", "{location}", "crawl", 2, 10,
            ai_provider="ollama", local_model="llama3.1:8b",
            local_url="http://localhost:11434", context_tokens=32768, local_timeout=600,
        )
        self.assertEqual(config.llm.provider, "ollama")
        self.assertEqual(config.llm.models, ["llama3.1:8b"])
        self.assertEqual(config.llm.verifier_model, "llama3.1:8b")
        self.assertEqual(config.llm.adjudicator_model, "llama3.1:8b")
        self.assertFalse(requires_openai("crawl", "ollama", False))
        self.assertTrue(requires_openai("hybrid", "ollama", False))
        self.assertFalse(requires_openai("crawl", "openai", True))
        self.assertTrue(requires_openai("crawl", "openai", False))
        switched = configure(
            config, "Fråga?", "språk", "{location}", "crawl", 2, 10, ai_provider="openai",
        )
        self.assertEqual(switched.llm.models, ["gpt-5-mini"])
        self.assertEqual(switched.llm.verifier_model, "gpt-5-mini")
        self.assertEqual(switched.llm.adjudicator_model, "gpt-5-mini")
        self.assertEqual(switched.llm.timeout_seconds, 120)

    def test_historical_codebook_requires_cutoff(self):
        base = load_config(ROOT / "examples/language_requirements.yaml")
        with self.assertRaises(ValueError):
            configure(base, "Question", "", "", "crawl", 2, 10)


if __name__ == "__main__":
    unittest.main()
