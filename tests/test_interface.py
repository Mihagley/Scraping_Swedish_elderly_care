import unittest
from pathlib import Path

from municipal_research.config import load_config, load_municipalities
from municipal_research.interface import configure, parse_locations

ROOT = Path(__file__).resolve().parents[1]


class InterfaceTests(unittest.TestCase):
    def test_registries_and_legacy_csv(self):
        kommun = load_municipalities(ROOT / 'examples/municipalities.csv')
        regions = load_municipalities(ROOT / 'examples/regions.csv')
        self.assertEqual(len(regions), 21)
        self.assertEqual(kommun[1].id, '0180')
        self.assertTrue(all(x.kind == 'kommun' for x in kommun))
        self.assertTrue(all(x.kind == 'region' for x in regions))
        self.assertEqual(len({x.id for x in kommun + regions}), 27)

    def test_upload_rejects_duplicate_ids_and_invalid_kind(self):
        for text in [
            'id,name,domains\nx,A,a.se\nx,B,b.se',
            'id,name,domains,kind\nx,A,a.se,county',
            'name,domains\nA,a.se',
        ]:
            with self.assertRaises(ValueError):
                parse_locations(text)

    def test_settings_are_validated_without_mutating_codebook(self):
        base = load_config(ROOT / 'examples/interface.yaml')
        config = configure(base, 'New question?', 'språkkrav, B2', '{location} {question}', 'crawl', 2, 10)
        self.assertEqual(config.discovery.keywords, ['språkkrav', 'B2'])
        self.assertEqual(config.research.labels, base.research.labels)
        self.assertNotEqual(config.research.question, base.research.question)
        for query in ['{location.name}', '{bad}', '{location!r}', '{']:
            with self.assertRaises(ValueError):
                configure(base, 'Question', '', query, 'crawl', 2, 10)
        with self.assertRaises(ValueError):
            configure(base, 'Question', '', '', 'crawl', 0, 10)

    def test_historical_codebook_requires_cutoff(self):
        base = load_config(ROOT / 'examples/language_requirements.yaml')
        with self.assertRaises(ValueError):
            configure(base, 'Question', '', '', 'crawl', 2, 10)


if __name__ == '__main__':
    unittest.main()
