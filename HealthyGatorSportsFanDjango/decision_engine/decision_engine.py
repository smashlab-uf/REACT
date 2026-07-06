"""
Decision engine for MSSD volatility detection

Author: Celia Mercier
"""

import pandas as pd


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


def add_within_person_threshold(df, threshold_quantile=0.80):
    df = df.copy()
    df = df.sort_values(["user_id", "timestamp"])

    df["user_threshold"] = (
        df.groupby("user_id")["observed_mssd"]
        .transform(
            lambda s: s.expanding(min_periods=3)
            .quantile(threshold_quantile)
            .shift(1)
        )
    )

    return df


def apply_decision_rules(
    df,
    threshold_quantile=0.80,
    cooldown_minutes=60,
    max_prompts_per_day=4
):
    df = df.copy()
    df = df.sort_values(["user_id", "timestamp"])

    df = add_within_person_threshold(df, threshold_quantile)

    df["eligible"] = False
    df["decision_reason"] = "below within-person threshold"

    for user_id in df["user_id"].unique():
        user_df = df[df["user_id"] == user_id]

        last_prompt_time = None
        prompts_by_day = {}

        for idx, row in user_df.iterrows():
            if pd.isna(row["ema"]) or pd.isna(row["observed_mssd"]):
                df.at[idx, "decision_reason"] = "missing or insufficient EMA data"

            elif pd.isna(row["user_threshold"]):
                df.at[idx, "decision_reason"] = "insufficient within-person history"

            elif row["observed_mssd"] <= row["user_threshold"]:
                df.at[idx, "decision_reason"] = "below within-person threshold"

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
                    df.at[idx, "decision_reason"] = "prompt sent"
                    last_prompt_time = row["timestamp"]
                    prompts_by_day[day] += 1

    df["eligible"] = df["decision_reason"] == "prompt sent"

    return df


def summarize_decisions(df):
    summary = df.groupby("user_id").agg(
        prompts_sent=("decision_reason", lambda x: (x == "prompt sent").sum()),
        average_mssd=("observed_mssd", "mean"),
        max_mssd=("observed_mssd", "max"),
        average_threshold=("user_threshold", "mean"),
        max_threshold=("user_threshold", "max"),
    )

    return summary.reset_index()


def validate_prompt_counts_vary(summary_df):
    unique_counts = summary_df["prompts_sent"].nunique()

    if unique_counts <= 1:
        raise AssertionError(
            "All users received the same prompt count. "
            "This may indicate the prompt-counting logic or per-user threshold is broken."
        )

    return True