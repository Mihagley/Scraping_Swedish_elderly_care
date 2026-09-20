import argparse

from recruitment_ads.download import annual_url, download


def main():
    p = argparse.ArgumentParser(description="Cache official annual JobTech full-text ads")
    p.add_argument("--years", nargs="+", type=int, required=True)
    p.add_argument("--cache", default="research/recruitment_ads/cache")
    p.add_argument(
        "--sample",
        action="store_true",
        help="Official 1%% development sample; never full-year coverage",
    )
    args = p.parse_args()
    for year in args.years:
        path, meta = download(
            annual_url(year, args.sample), args.cache, year=year, sample=args.sample
        )
        print(path, meta["source_hash"], flush=True)


if __name__ == "__main__":
    main()
