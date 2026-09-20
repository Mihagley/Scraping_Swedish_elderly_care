"""Export the separate historical recruitment cohorts without joining primary trends."""

import argparse
import json
from pathlib import Path

import matplotlib
import pandas as pd

from recruitment_ads.export import export_xlsx

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import PercentFormatter  # noqa: E402

COHORTS = {
    "mapped_occupation_sensitivity": "Mapped occupations · sensitivity",
    "unmapped_title_context_exploratory": "Unmapped occupation titles · exploratory",
}
COLORS = ["#176B67", "#A34B28", "#466B9B", "#815A91", "#8C753B"]


def figures(output):
    output = Path(output)
    destination = output / "figures"
    destination.mkdir(exist_ok=True)
    national = pd.read_csv(output / "tables/National_Trends.csv")
    occupations = pd.read_csv(
        output / "tables/Occupation_Trends.csv", dtype={"occupation_group": str}
    )
    panel = pd.read_parquet(output / "municipality_year_historical.parquet")
    panel = panel[panel.occupation_group == "overall"]
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )

    def layout(title, percentage=True):
        fig, axes = plt.subplots(1, 2, figsize=(14, 5.7), sharey=percentage)
        fig.suptitle(title, fontsize=17, fontweight="bold", x=0.06, ha="left")
        for ax, name in zip(axes, COHORTS.values(), strict=True):
            ax.set_title(name, loc="left", pad=12)
            ax.grid(axis="y", alpha=0.2)
            ax.set_axisbelow(True)
            if percentage:
                ax.yaxis.set_major_formatter(PercentFormatter(1))
                ax.set_ylim(0, 1)
        fig.text(
            0.06,
            0.035,
            "Unvalidated recruitment wording; no policy inference. Cohorts remain separate. Missing denominators stay missing.\nLines stop at source-period boundaries (2015/16 and 2020/21); changes may reflect coverage, employer names or occupation coding.",
            fontsize=9,
            color="#4A5560",
        )
        fig.subplots_adjust(left=0.06, right=0.98, top=0.82, bottom=0.22, wspace=0.18)
        return fig, axes

    def segmented(ax, rows, field, label, color):
        rows = rows.sort_values("year")
        for i, (lo, hi) in enumerate([(2006, 2015), (2016, 2020), (2021, 2025)]):
            part = rows[rows.year.between(lo, hi)]
            ax.plot(
                part.year,
                part[field],
                marker="o",
                markersize=3.5,
                linewidth=1.5,
                color=color,
                label=label if i == 0 else None,
            )
        ax.set_xticks([2006, 2010, 2015, 2020, 2025])
        ax.axvline(2015.5, color="#929AA1", linestyle=":", linewidth=1)
        ax.axvline(2020.5, color="#929AA1", linestyle=":", linewidth=1)

    def save(fig, name):
        for ext in ("png", "svg"):
            fig.savefig(destination / f"{name}.{ext}", dpi=180, facecolor="white")
        plt.close(fig)

    for name, title, fields in [
        (
            "01_required",
            "Swedish requirements in retained municipal recruitment records",
            [
                ("share_required_ads", "Record weighted"),
                ("standardised_share_required", "Fixed SSYK weights (0.5 / 0.5)"),
            ],
        ),
        (
            "03_categories",
            "Formal thresholds and qualitative language requirements",
            [
                ("share_formal_threshold", "Formal threshold"),
                ("share_strong_qualitative", "Strong qualitative"),
                ("share_functional", "Functional"),
                ("share_generic", "Generic"),
            ],
        ),
        (
            "04_formal_levels",
            "Explicit course, CEFR and language-test requirements",
            [
                ("share_sfi", "SFI"),
                ("share_swedish_course", "Swedish / SVA courses"),
                ("share_gers_b1", "B1"),
                ("share_gers_b2", "B2"),
                ("share_language_test", "Language test"),
            ],
        ),
    ]:
        fig, axes = layout(title)
        for ax, cohort in zip(axes, COHORTS, strict=True):
            rows = national[national.historical_cohort == cohort]
            for (field, label), color in zip(fields, COLORS, strict=False):
                if rows[field].notna().any():
                    segmented(ax, rows, field, label, color)
            ax.legend(loc="upper left", fontsize=8, frameon=False)
        save(fig, name)

    fig, axes = layout("Swedish requirements by occupation group")
    for ax, cohort in zip(axes, COHORTS, strict=True):
        rows = occupations[occupations.historical_cohort == cohort]
        for (group, values), color in zip(rows.groupby("occupation_group"), COLORS, strict=False):
            label = {
                "5321": "SSYK 5321",
                "5330": "SSYK 5330",
                "title_underskoterska": "Undersköterska title",
                "title_vardbitrade": "Vårdbiträde title",
                "title_mixed_frontline": "Both frontline titles",
            }.get(group, group)
            segmented(ax, values, "share_required_ads", label, color)
        ax.legend(loc="upper left", fontsize=8, frameon=False)
    save(fig, "02_occupations")

    fig, axes = layout("Municipality distributions · cells with at least 20 records")
    years = [2006, 2010, 2015, 2017, 2021, 2024]
    for ax, cohort in zip(axes, COHORTS, strict=True):
        rows = panel[(panel.historical_cohort == cohort) & (panel.n_ads >= 20)]
        labels = []
        for i, year in enumerate(years):
            values = rows[rows.year == year].share_required_ads.dropna()
            labels.append(f"{year}\nn={len(values)}")
            if len(values):
                ax.boxplot(
                    [values],
                    positions=[i],
                    widths=0.55,
                    patch_artist=True,
                    boxprops={"facecolor": "#B9D9D3"},
                    medianprops={"color": "#176B67"},
                )
        ax.set_xticks(range(len(years)), labels)
        ax.set_xlim(-0.6, len(years) - 0.4)
    save(fig, "05_municipality_distribution")

    fig, axes = layout(
        "Municipalities with sufficient observed recruitment coverage", percentage=False
    )
    for ax, cohort in zip(axes, COHORTS, strict=True):
        rows = panel[panel.historical_cohort == cohort]
        coverage = (
            rows.groupby("year")
            .n_ads.agg(
                good=lambda v: int((v >= 20).sum()), moderate_or_good=lambda v: int((v >= 5).sum())
            )
            .reset_index()
        )
        segmented(ax, coverage, "good", "At least 20 records", COLORS[0])
        segmented(ax, coverage, "moderate_or_good", "At least 5 records", COLORS[1])
        ax.set_ylim(0, 290)
        ax.set_ylabel("Number of municipalities")
        ax.legend(loc="upper left", fontsize=8, frameon=False)
    save(fig, "06_coverage")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", default="research/recruitment_ads/output-historical-v1")
    p.add_argument("--figures-only", action="store_true")
    p.add_argument("--skip-review-workbook", action="store_true")
    args = p.parse_args()
    output = Path(args.output)
    figures(output)
    if not args.figures_only:
        for name, target, review in [
            ("workbook_tables.json", "recruitment_language_requirements.xlsx", False),
            ("review_workbook_tables.json", "validation.xlsx", True),
        ]:
            if review and args.skip_review_workbook:
                continue
            tables = {
                k: pd.DataFrame(v)
                for k, v in json.loads((output / name).read_text(encoding="utf-8")).items()
            }
            export_xlsx(tables, output / target, preserve_existing=review)


if __name__ == "__main__":
    main()
