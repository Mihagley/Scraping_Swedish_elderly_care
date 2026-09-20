import argparse
from pathlib import Path

import pyarrow.parquet as pq

from recruitment_ads.pipeline import read_config
from recruitment_ads.validate import (
    false_negative_metrics,
    import_reviews,
    metrics,
    preserve_review,
    validation_samples,
)


def main():
    p = argparse.ArgumentParser(description="Generate or score preserved human review files")
    p.add_argument("--config", default="research/recruitment_ads/config.yaml")
    p.add_argument("--output", default="research/recruitment_ads/output")
    p.add_argument("--import-workbook")
    args = p.parse_args()
    output = Path(args.output)
    config = read_config(args.config)
    if args.import_workbook:
        import_reviews(args.import_workbook, output / "validation.csv")
        import_reviews(args.import_workbook, output / "false_negatives.csv", "False_Negatives")
    records = pq.read_table(output / "ads_classified.parquet").to_pylist()
    sample, negatives, inventory = validation_samples(
        records, config["validation_quotas"], config["seed"], config["false_negative_sample_size"]
    )
    sample = preserve_review(output / "validation.csv", sample)
    negatives = preserve_review(output / "false_negatives.csv", negatives)
    inventory.to_csv(output / "validation_sampling_inventory.csv", index=False)
    metrics(sample).to_csv(output / "validation_metrics.csv", index=False)
    false_negative_metrics(negatives).to_csv(output / "false_negative_metrics.csv", index=False)
    print(f"Review ads: {len(sample)}; random predicted negatives: {len(negatives)}")


if __name__ == "__main__":
    main()
