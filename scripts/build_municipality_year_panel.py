import argparse
from pathlib import Path

from recruitment_ads.export import export_xlsx, figures, prepare_tables


def main():
    p = argparse.ArgumentParser(
        description="Export recruitment measures, coverage and review workbooks"
    )
    p.add_argument("--config", default="research/recruitment_ads/config.yaml")
    p.add_argument("--master", default="research/recruitment_ads/employer_master.csv")
    p.add_argument("--output", default="research/recruitment_ads/output")
    p.add_argument("--tables-only", action="store_true")
    p.add_argument(
        "--skip-review-workbook",
        action="store_true",
        help="Preserve an already edited validation workbook",
    )
    args = p.parse_args()
    tables, reviews = prepare_tables(args.output, args.master, args.config)
    figures(tables, args.output)
    if not args.tables_only:
        export_xlsx(tables, Path(args.output) / "recruitment_language_requirements.xlsx")
        if not args.skip_review_workbook:
            export_xlsx(reviews, Path(args.output) / "validation.xlsx", preserve_existing=True)


if __name__ == "__main__":
    main()
