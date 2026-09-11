"""Run the synthetic decision engine and optional BBI HRV audit."""

import argparse
from pathlib import Path

import pandas as pd

from decision_engine.decision_engine import (
    apply_decision_rules,
    attach_rmssd_to_decisions,
    calculate_mssd,
    compute_rmssd_5min_series,
    load_bbi_csv,
    merge_hr_to_prompts,
    summarize_decisions,
    validate_prompt_counts_vary,
)
from syntheticData.synthetic_generator import (
    generate_HR,
    generate_cohort,
    generate_user_ids,
)


def spread_ema_timestamps(dataframe, ema_per_day):
    result = dataframe.copy()
    base_date = pd.Timestamp("2026-06-01")
    hours_by_prompt = [9, 12, 15, 18, 21]

    def make_timestamp(row):
        day_offset = int(row["prompt_idx"]) // ema_per_day
        prompt_in_day = int(row["prompt_idx"]) % ema_per_day
        return base_date + pd.Timedelta(
            days=day_offset,
            hours=hours_by_prompt[prompt_in_day],
        )

    result["timestamp"] = result.apply(make_timestamp, axis=1)
    return result


def build_decisions(users=100, days=7, ema_per_day=5, seed=42):
    user_ids = generate_user_ids(users)
    cohort = []
    for index, user_id in enumerate(user_ids):
        cohort.append(generate_cohort(
            users=1,
            days=days,
            ema_per_day=ema_per_day,
            seed=seed + index,
            resp_rate=0.65 + (index % 8) * 0.04,
            user_ids=[user_id],
        ))

    ema = spread_ema_timestamps(pd.concat(cohort, ignore_index=True), ema_per_day)
    heart_rate = generate_HR(
        users=users,
        days=days,
        seed=seed,
        user_ids=user_ids,
    )
    decisions = apply_decision_rules(
        calculate_mssd(merge_hr_to_prompts(ema, heart_rate)),
        threshold_quantile=0.80,
        cooldown_minutes=60,
        max_prompts_per_day=4,
    )
    return decisions


def write_outputs(decisions, output_dir, bbi_path=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_decisions(decisions)
    decisions.to_csv(output_dir / "decision_log.csv", index=False)
    decisions.to_json(output_dir / "decision_log.json", orient="records", date_format="iso", indent=2)
    summary.to_csv(output_dir / "decision_summary.csv", index=False)
    summary.to_json(output_dir / "decision_summary.json", orient="records", date_format="iso", indent=2)
    validate_prompt_counts_vary(summary)

    if bbi_path:
        bbi = load_bbi_csv(bbi_path)
        hrv_per_decision = attach_rmssd_to_decisions(decisions, bbi)
        hrv_per_decision_output = hrv_per_decision[
            [
                "user_id", "timestamp", "rmssd_bbi", "rmssd_count",
                "rmssd_baseline", "rmssd_ratio", "hrv_class",
            ]
        ]
        hrv_per_decision_output.to_csv(
            output_dir / "hrv_per_decision.csv", index=False
        )
        hrv_per_decision_output.to_json(
            output_dir / "hrv_per_decision.json",
            orient="records",
            date_format="iso",
            indent=2,
        )

        hrv_series = compute_rmssd_5min_series(bbi)
        hrv_series.to_csv(output_dir / "hrv_5min_series.csv", index=False)
        hrv_series.to_json(
            output_dir / "hrv_5min_series.json",
            orient="records",
            date_format="iso",
            indent=2,
        )

    return decisions, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--ema-per-day", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bbi-file", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).with_name("outputs"),
    )
    args = parser.parse_args()
    if args.bbi_file and not args.bbi_file.is_file():
        parser.error(f"BBI file not found: {args.bbi_file}")
    decisions, summary = write_outputs(
        build_decisions(args.users, args.days, args.ema_per_day, args.seed),
        args.output_dir,
        args.bbi_file,
    )
    print(decisions[[
        "user_id", "timestamp", "ema", "observed_mssd",
        "user_threshold", "send_prompt", "decision_reason",
    ]].head(30))
    print(summary.head())
    print("DONE RUNNING DECISION ENGINE")


if __name__ == "__main__":
    main()