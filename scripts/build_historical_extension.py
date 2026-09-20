import argparse
import json
from pathlib import Path

import pyarrow.parquet as pq

from recruitment_ads.download import download
from recruitment_ads.employer import EmployerMaster
from recruitment_ads.historical_pipeline import export_historical, run_historical
from recruitment_ads.pipeline import read_config
from recruitment_ads.validate import import_reviews


def main():
    p = argparse.ArgumentParser(
        description="Separate 2006–2015 and 2016–2020 recruitment sensitivity cases"
    )
    p.add_argument("--years", nargs="+", type=int, default=[2006, 2010, 2015])
    p.add_argument("--download", action="store_true")
    p.add_argument("--export-only", action="store_true")
    p.add_argument("--import-workbook")
    p.add_argument("--cache", default="research/recruitment_ads/cache/historical")
    p.add_argument("--config", default="research/recruitment_ads/config.yaml")
    p.add_argument("--master", default="research/recruitment_ads/employer_master.csv")
    p.add_argument("--aliases", default="research/recruitment_ads/employer_aliases.csv")
    p.add_argument(
        "--taxonomy",
        default="research/recruitment_ads/cache/taxonomy/legacy_get-occupation-name-with-relations.json",
    )
    p.add_argument("--recent-output", default="research/recruitment_ads/output")
    p.add_argument("--output", default="research/recruitment_ads/output-historical-pilot-v1")
    args = p.parse_args()
    if args.import_workbook and not args.export_only:
        p.error("--import-workbook requires --export-only")
    if args.export_only:
        output = Path(args.output)
        if args.import_workbook:
            import_reviews(args.import_workbook, output / "validation.csv")
            import_reviews(args.import_workbook, output / "false_negatives.csv", "False_Negatives")
            import_reviews(
                args.import_workbook, output / "excluded_cases_review.csv", "Excluded_Cases"
            )
        export_historical(
            pq.read_table(output / "ads_historical.parquet").to_pylist(),
            pq.read_table(output / "recruitment_spells_historical.parquet").to_pylist(),
            EmployerMaster.load(
                output / "reproduction/employer_master.csv",
                output / "reproduction/employer_aliases.csv",
            ).rows,
            json.loads((output / "historical_source_inventory.json").read_text()),
            json.loads((output / "recent_source_inventory.json").read_text()),
            output,
            read_config(output / "reproduction/config.yaml"),
        )
        return
    if args.download:
        for year in args.years:
            if not 2006 <= year <= 2015:
                raise ValueError("Older annual downloads accept 2006–2015 only")
            download(
                f"https://data.jobtechdev.se/annonser/historiska/{year}.jsonl.zip",
                args.cache,
                year=year,
                data_version="original-jsonl-2023",
                sample=False,
            )
    run_historical(
        args.config,
        args.master,
        args.aliases,
        args.taxonomy,
        args.cache,
        args.recent_output,
        args.output,
        args.years,
    )


if __name__ == "__main__":
    main()
