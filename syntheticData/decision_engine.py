"""
Decision engine for MSSD volatility detection

Author: Celia Mercier
"""

import pandas as pd
import os
import argparse
import logging


logger = logging.getLogger(__name__)


def calculate_mssd(df, window=3):
    df = df.copy()
    df = df.sort_values(["user_id", "timestamp"])

    df["ema_diff_squared"] = df.groupby("user_id")["ema"].diff() ** 2

    df["observed_mssd"] = (
        df.groupby("user_id")["ema_diff_squared"]
        .rolling(window=window, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )

    return df


def apply_decision_rules(df, threshold_quantile=0.80, cooldown_minutes=5, max_prompts_per_day=4):
    df = df.copy()
    df = df.sort_values(["user_id", "timestamp"])

    df["send_prompt"] = False
    df["decision_reason"] = "below threshold"

    threshold = df["observed_mssd"].quantile(threshold_quantile)

    for user_id in df["user_id"].unique():
        user_df = df[df["user_id"] == user_id]

        last_prompt_time = None
        prompts_by_day = {}

        for idx, row in user_df.iterrows():
            if pd.isna(row["observed_mssd"]):
                df.at[idx, "decision_reason"] = "missing or insufficient EMA data"

            elif row["observed_mssd"] < threshold:
                df.at[idx, "decision_reason"] = "below threshold"

            else:
                day = row["timestamp"].date()

                if day not in prompts_by_day:
                    prompts_by_day[day] = 0

                if prompts_by_day[day] >= max_prompts_per_day:
                    df.at[idx, "decision_reason"] = "daily cap reached"

                elif last_prompt_time is not None and (
                    row["timestamp"] - last_prompt_time
                ).total_seconds() / 60 < cooldown_minutes:
                    df.at[idx, "decision_reason"] = "cooldown active"

                else:
                    df.at[idx, "send_prompt"] = True
                    df.at[idx, "decision_reason"] = "prompt sent"
                    last_prompt_time = row["timestamp"]
                    prompts_by_day[day] += 1

    return df


def summarize_decisions(df):
    summary = df.groupby("user_id").agg(
        prompts_sent=("send_prompt", "sum"),
        average_mssd=("observed_mssd", "mean"),
        max_mssd=("observed_mssd", "max"),
    )

    return summary.reset_index()


def load_ema_csv(csv_path):
    df = pd.read_csv(csv_path)
    required_columns = {"user_id", "timestamp", "mood"}
    missing_columns = required_columns - set(df.columns)

    if missing_columns:
        raise ValueError(
            f"EMA CSV is missing required columns: {sorted(missing_columns)}"
        )

    if df.empty:
        raise ValueError("EMA CSV has no rows to process.")

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["ema"] = pd.to_numeric(df["mood"], errors="coerce")
    df = df.dropna(subset=["user_id", "timestamp", "ema"])

    if df.empty:
        raise ValueError("EMA CSV has no usable rows after parsing.")

    return df


def run_engine(csv_path, window=3, threshold_quantile=0.80):
    logger.info("Loading EMA sample data from %s", csv_path)
    ema_df = load_ema_csv(csv_path)
    logger.info(
        "Loaded %s EMA rows for %s users",
        len(ema_df),
        ema_df["user_id"].nunique(),
    )

    mssd_df = calculate_mssd(ema_df, window=window)
    decision_df = apply_decision_rules(
        mssd_df,
        threshold_quantile=threshold_quantile,
    )
    summary_df = summarize_decisions(decision_df)

    prompt_count = int(decision_df["send_prompt"].sum())
    logger.info("Decision engine completed.")
    logger.info("Prompt decisions generated: %s", prompt_count)
    logger.info("Users summarized: %s", len(summary_df))
    logger.info(
        "Average observed MSSD: %.4f",
        summary_df["average_mssd"].mean(),
    )

    return decision_df, summary_df


def main():
    default_csv = os.path.join(
        os.path.dirname(__file__),
        "csv_export",
        "app_ema.csv",
    )

    parser = argparse.ArgumentParser(
        description="Run the MSSD decision engine on sample EMA CSV data."
    )
    parser.add_argument("--csv", default=default_csv, help="Path to app_ema.csv")
    parser.add_argument("--window", type=int, default=3, help="MSSD rolling window")
    parser.add_argument(
        "--threshold-quantile",
        type=float,
        default=0.80,
        help="Quantile threshold used by the decision rules",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    run_engine(
        csv_path=args.csv,
        window=args.window,
        threshold_quantile=args.threshold_quantile,
    )


if __name__ == "__main__":
    main()
