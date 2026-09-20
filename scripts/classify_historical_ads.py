import argparse

from recruitment_ads.pipeline import read_config, run_classification


def main():
    p = argparse.ArgumentParser(
        description="Stream and classify a pilot or an explicitly selected full run"
    )
    p.add_argument("--config", default="research/recruitment_ads/config.yaml")
    p.add_argument("--master", default="research/recruitment_ads/employer_master.csv")
    p.add_argument("--aliases", default="research/recruitment_ads/employer_aliases.csv")
    p.add_argument("--cache", default="research/recruitment_ads/cache")
    p.add_argument("--output", default="research/recruitment_ads/output")
    p.add_argument(
        "--full",
        action="store_true",
        help="Use after pilot/manual validation checkpoint; all municipalities and primary years",
    )
    args = p.parse_args()
    c = read_config(args.config)
    run_classification(
        args.config,
        args.master,
        args.aliases,
        args.cache,
        args.output,
        c["years"] if args.full else c["pilot_years"],
        None if args.full else c["pilot_municipalities"],
    )


if __name__ == "__main__":
    main()
