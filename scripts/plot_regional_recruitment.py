"""Reproducible county small multiples for separate historical recruitment cohorts."""

import argparse
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import PercentFormatter  # noqa: E402


def figures(output):
    output = Path(output)
    destination = output / "figures"
    destination.mkdir(exist_ok=True)
    panel = pd.read_parquet(output / "region_year.parquet")
    panel = panel[panel.occupation_group == "overall"]
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    for cohort, field, name, subtitle, color in [
        (
            "mapped_occupation_sensitivity",
            "share_required_ads",
            "01_county_trends_mapped",
            "Occupation-mapped cases · percentage of eligible ads requiring Swedish",
            "#176B67",
        ),
        (
            "mapped_occupation_sensitivity",
            "standardised_share_required",
            "02_county_trends_standardised",
            "Occupation-mapped cases · fixed weights: 50% SSYK 5321 and 50% SSYK 5330",
            "#A34B28",
        ),
        (
            "unmapped_title_context_exploratory",
            "share_required_ads",
            "03_county_trends_exploratory",
            "Unmapped occupation titles · exploratory cases kept separate",
            "#466B9B",
        ),
    ]:
        frame = panel[panel.historical_cohort == cohort]
        fig, axes = plt.subplots(7, 3, figsize=(15, 18), sharex=True, sharey=True)
        fig.suptitle(
            "Swedish-language recruitment requirements by county",
            x=0.06,
            ha="left",
            y=0.985,
            fontsize=21,
            fontweight="bold",
        )
        fig.text(0.06, 0.961, subtitle, fontsize=12)
        for ax, (_, rows) in zip(axes.flat, frame.groupby("region_id", sort=True), strict=True):
            rows = rows.sort_values("year").set_index("year").reindex(range(2006, 2026))
            ax.set_title(
                rows.region_name.dropna().iloc[0],
                loc="left",
                fontsize=12,
                fontweight="bold",
                pad=6,
            )
            for lo, hi in [(2006, 2015), (2016, 2020), (2021, 2025)]:
                part = rows.loc[lo:hi]
                ax.plot(part.index, part[field], color=color, linewidth=1.4)
            if field == "standardised_share_required":
                low = rows[["n_ads_5321", "n_ads_5330"]].min(axis=1) < 5
            else:
                low = rows.n_ads < 20
            for selected, fill in [(~low, color), (low, "white")]:
                part = rows[selected & rows[field].notna()]
                ax.scatter(
                    part.index,
                    part[field],
                    s=17,
                    facecolors=fill,
                    edgecolors=color,
                    linewidths=0.9,
                    zorder=3,
                )
            ax.axvline(2015.5, linestyle=":", color="#A1A9AE", linewidth=0.8)
            ax.axvline(2020.5, linestyle=":", color="#A1A9AE", linewidth=0.8)
            ax.set_ylim(-0.035, 1.04)
            ax.set_xlim(2005.5, 2025.5)
            ax.set_yticks([0, 0.5, 1])
            ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
            ax.set_xticks([2006, 2010, 2015, 2020, 2025])
            ax.tick_params(labelbottom=True, labelsize=8)
            ax.grid(axis="y", alpha=0.2)
            ax.set_axisbelow(True)
            last = rows.loc[2025]
            n = int(last.n_ads) if pd.notna(last.n_ads) else 0
            ax.text(
                0.99,
                0.04,
                f"2025: {n:,} ads",
                transform=ax.transAxes,
                ha="right",
                va="bottom",
                fontsize=8,
                color="#626C74",
            )
        low_note = (
            "Hollow markers: one occupation has fewer than 5 ads; both occupation denominators are required."
            if field == "standardised_share_required"
            else "Hollow markers: fewer than 20 ads. Missing denominators are gaps, not zero percentages."
        )
        fig.text(
            0.06,
            0.035,
            low_note
            + "\nSource-period breaks: 2015/16 and 2020/21. Fixed SCB 2026 county grouping by municipal employer.\nUnvalidated recruitment wording. Historical coverage differs across counties and years; no formal policy inference.",
            fontsize=10,
            color="#46535D",
            linespacing=1.5,
        )
        fig.subplots_adjust(left=0.06, right=0.98, top=0.935, bottom=0.10, hspace=0.55, wspace=0.22)
        for suffix in ["png", "svg"]:
            fig.savefig(destination / f"{name}.{suffix}", dpi=180, facecolor="white")
        plt.close(fig)
    # Compact overview for county-by-year comparison, using a shared percentage scale.
    frame = panel[panel.historical_cohort == "mapped_occupation_sensitivity"]
    codes = sorted(frame.region_id.unique())
    grid = frame.pivot(index="region_id", columns="year", values="share_required_ads").reindex(
        codes
    )
    counts = frame.pivot(index="region_id", columns="year", values="n_ads").reindex(codes)
    names = frame.drop_duplicates("region_id").set_index("region_id").region_name
    fig, ax = plt.subplots(figsize=(13, 10))
    cmap = plt.get_cmap("YlGnBu").copy()
    cmap.set_bad("#D9DEE3")
    chart = ax.imshow(grid, vmin=0, vmax=1, cmap=cmap, aspect="auto")
    ax.set_yticks(range(21), [names[code] for code in codes])
    ax.set_xticks(range(20), range(2006, 2026), rotation=45, ha="right")
    ax.tick_params(length=0)
    for y in range(21):
        for x in range(20):
            if pd.notna(grid.iloc[y, x]) and counts.iloc[y, x] < 20:
                ax.text(
                    x,
                    y,
                    "·",
                    ha="center",
                    va="center",
                    color="#CB4527",
                    fontsize=19,
                    fontweight="bold",
                )
    for x in (9.5, 14.5):
        ax.axvline(x, color="white", linewidth=2)
    fig.colorbar(chart, ax=ax, fraction=0.025, pad=0.02, format=PercentFormatter(1)).set_label(
        "Ads classified as requiring Swedish"
    )
    ax.set_title(
        "Swedish-language requirements by county and year\nOccupation-mapped municipal elderly-care recruitment",
        loc="left",
        fontsize=17,
        pad=18,
    )
    fig.text(
        0.23,
        0.035,
        "Grey = no eligible ads. Orange dot = fewer than 20 ads.\nFixed SCB 2026 county grouping. Unvalidated wording measures; source periods remain separate.",
        fontsize=10,
    )
    fig.subplots_adjust(left=0.23, right=0.88, top=0.90, bottom=0.14)
    for suffix in ["png", "svg"]:
        fig.savefig(destination / f"04_county_year_overview.{suffix}", dpi=180, facecolor="white")
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", default="research/recruitment_ads/output-regions-v2")
    args = p.parse_args()
    figures(args.output)


if __name__ == "__main__":
    main()
