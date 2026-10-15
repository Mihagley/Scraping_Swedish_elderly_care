"""Validated settings shared by the browser interface and tests."""
from __future__ import annotations

import csv
import io
from string import Formatter

from .config import Config, Location


def parse_locations(text: str) -> list[Location]:
    rows = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not {'id', 'name', 'domains'} <= set(rows.fieldnames or []):
        raise ValueError('CSV needs id, name and domains columns; kind and seeds are optional.')
    locations = [Location(
        id=row['id'], name=row['name'], kind=row.get('kind') or 'kommun',
        domains=[x.strip() for x in row['domains'].split(';') if x.strip()],
        seeds=[x.strip() for x in (row.get('seeds') or '').split(';') if x.strip()],
    ) for row in rows]
    if not locations or len({x.id for x in locations}) != len(locations):
        raise ValueError('Locations must be nonempty with unique IDs across both types.')
    return locations


def configure(base: Config, question: str, keywords: str, queries: str,
              provider: str, max_documents: int, max_calls: int, cutoff=None) -> Config:
    if not question.strip():
        raise ValueError('Enter a research question.')
    query_list = [line.strip() for line in queries.splitlines() if line.strip()]
    for query in query_list:
        for _, field, spec, conversion in Formatter().parse(query):
            if field is not None and (field not in {'location', 'municipality', 'region', 'domain', 'question'} or spec or conversion):
                raise ValueError('Queries support only {location}, {municipality}, {region}, {domain}, {question}.')
    data = base.model_dump(mode='json')
    data['research'].update(question=question.strip(), cutoff=cutoff)
    data['discovery'].update(
        keywords=[x.strip() for x in keywords.replace('\n', ',').split(',') if x.strip()],
        queries=query_list, provider=provider, max_documents=max_documents,
    )
    data['llm']['max_calls'] = max_calls
    return Config.model_validate(data)
