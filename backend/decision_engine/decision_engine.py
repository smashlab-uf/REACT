"""
Decision engine for MSSD volatility detection

Author: Celia Mercier
"""

import math

import pandas as pd


def calculate_mssd(df, window=3):
    # Rolling window (default 3) gives a recency-weighted MSSD signal suitable
    # for real-time triggering. Offline analysis using cumulative MSSD will
    # produce different values; use JITAILog.observed_mssd as the audit source.
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

    df["send_prompt"] = False
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

    df["send_prompt"] = df["decision_reason"] == "prompt sent"

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


def merge_hr_to_prompts(ema_df, hr_df, tolerance="30min"):
    required_ema = {"user_id", "timestamp"}
    required_hr = {"user_id", "timestamp"}
    missing_ema = required_ema - set(ema_df.columns)
    missing_hr = required_hr - set(hr_df.columns)
    if missing_ema:
        raise ValueError(f"EMA dataframe missing columns: {sorted(missing_ema)}")
    if missing_hr:
        raise ValueError(f"HR dataframe missing columns: {sorted(missing_hr)}")

    ema = ema_df.copy()
    heart_rate = hr_df.copy()
    if "hr" not in heart_rate.columns and "bpm" in heart_rate.columns:
        heart_rate = heart_rate.rename(columns={"bpm": "hr"})
    if "hr" not in heart_rate.columns:
        raise ValueError("HR dataframe must include 'hr' or 'bpm'")

    ema["timestamp"] = pd.to_datetime(ema["timestamp"])
    heart_rate["timestamp"] = pd.to_datetime(heart_rate["timestamp"])
    return pd.merge_asof(
        ema.sort_values(["timestamp", "user_id"]),
        heart_rate[["user_id", "timestamp", "hr"]].sort_values(
            ["timestamp", "user_id"]
        ),
        on="timestamp",
        by="user_id",
        direction="backward",
        tolerance=pd.Timedelta(tolerance),
    )


def _find_header_row_csv(path, marker="timezone,"):
    with open(path, "r", encoding="utf-8-sig") as file_handle:
        for row_number, line in enumerate(file_handle):
            if line.startswith(marker):
                return row_number
    raise ValueError("BBI CSV header row not found")


def _participant_id_from_metadata(path):
    with open(path, "r", encoding="utf-8-sig") as file_handle:
        lines = [file_handle.readline() for _ in range(20)]
    for index, line in enumerate(lines):
        if line.startswith("projectId") and index + 1 < len(lines):
            keys = line.strip().split(",")
            values = lines[index + 1].strip().split(",")
            if "participantId" in keys:
                participant_index = keys.index("participantId")
                if participant_index < len(values):
                    return values[participant_index]
    return None


def load_bbi_csv(path, confidence_col="confidence", min_confidence=1):
    header_row = _find_header_row_csv(path)
    bbi = pd.read_csv(path, skiprows=header_row)
    if "isoDate" in bbi.columns:
        timestamp = pd.to_datetime(bbi["isoDate"], errors="coerce", utc=True)
    elif "unixTimestampInMs" in bbi.columns:
        timestamp = pd.to_datetime(
            bbi["unixTimestampInMs"], errors="coerce", unit="ms", utc=True
        )
    else:
        raise ValueError("BBI CSV missing timestamp column")
    bbi["timestamp"] = timestamp.dt.tz_localize(None)

    if "participantId" in bbi.columns:
        bbi = bbi.rename(columns={"participantId": "user_id"})
    else:
        bbi["user_id"] = _participant_id_from_metadata(path)
    if bbi["user_id"].isna().all():
        raise ValueError("Could not determine participant/user ID from BBI CSV")
    if "bbi" not in bbi.columns:
        raise ValueError("BBI CSV missing 'bbi' column")
    bbi["bbi"] = pd.to_numeric(bbi["bbi"], errors="coerce")

    if confidence_col in bbi.columns:
        bbi[confidence_col] = pd.to_numeric(
            bbi[confidence_col], errors="coerce"
        ).fillna(0).astype(int)
        bbi = bbi[bbi[confidence_col] >= min_confidence]

    bbi = bbi.dropna(subset=["user_id", "timestamp", "bbi"])
    bbi = bbi[bbi["bbi"].between(300, 2000)]
    keep_columns = ["user_id", "timestamp", "bbi"]
    if confidence_col in bbi.columns:
        keep_columns.append(confidence_col)
    return bbi[keep_columns].rename(columns={"bbi": "bbi_ms"}).sort_values(
        ["user_id", "timestamp"]
    ).reset_index(drop=True)


def compute_rmssd_from_bbi(bbi_ms_series):
    values = pd.to_numeric(bbi_ms_series, errors="coerce").dropna().to_numpy()
    if len(values) < 2:
        return float("nan")
    return float(math.sqrt(((values[1:] - values[:-1]) ** 2).mean()))


def attach_rmssd_to_decisions(
    decision_df,
    bbi_df,
    window_seconds=300,
    min_beats=5,
    low_cutoff=0.8,
    high_cutoff=1.2,
):
    required = {"user_id", "timestamp", "bbi_ms"}
    missing = required - set(bbi_df.columns)
    if missing:
        raise ValueError(f"BBI dataframe missing columns: {sorted(missing)}")

    result = decision_df.copy()
    result["rmssd_bbi"] = float("nan")
    result["rmssd_count"] = 0
    result["rmssd_baseline"] = float("nan")
    result["rmssd_ratio"] = float("nan")
    result["hrv_class"] = pd.NA
    bbi = bbi_df.copy()
    bbi["timestamp"] = pd.to_datetime(bbi["timestamp"]).dt.tz_localize(None)
    history_by_user = {}

    for index, row in result.sort_values(["user_id", "timestamp"]).iterrows():
        user_bbi = bbi[bbi["user_id"] == row["user_id"]]
        start = row["timestamp"] - pd.Timedelta(seconds=window_seconds)
        window = user_bbi[
            (user_bbi["timestamp"] >= start)
            & (user_bbi["timestamp"] < row["timestamp"])
        ]
        result.at[index, "rmssd_count"] = len(window)
        if len(window) < min_beats:
            continue

        rmssd = compute_rmssd_from_bbi(window["bbi_ms"])
        result.at[index, "rmssd_bbi"] = rmssd
        history = history_by_user.setdefault(row["user_id"], [])
        baseline = float(pd.Series(history).median()) if history else float("nan")
        result.at[index, "rmssd_baseline"] = baseline
        if not pd.isna(rmssd) and not pd.isna(baseline) and baseline > 0:
            ratio = rmssd / baseline
            result.at[index, "rmssd_ratio"] = ratio
            result.at[index, "hrv_class"] = (
                "Low" if ratio < low_cutoff
                else "High" if ratio > high_cutoff
                else "Balanced"
            )
        if not pd.isna(rmssd):
            history.append(rmssd)

    return result


def compute_rmssd_5min_series(
    bbi_df,
    interval_seconds=300,
    min_beats=5,
    low_cutoff=0.8,
    high_cutoff=1.2,
):
    required = {"user_id", "timestamp", "bbi_ms"}
    missing = required - set(bbi_df.columns)
    if missing:
        raise ValueError(f"BBI dataframe missing columns: {sorted(missing)}")

    results = []
    bbi = bbi_df.copy()
    bbi["timestamp"] = pd.to_datetime(bbi["timestamp"]).dt.tz_localize(None)
    for user_id, group in bbi.groupby("user_id"):
        group = group.sort_values("timestamp")
        if group.empty:
            continue
        start = group["timestamp"].min().floor(f"{interval_seconds}s")
        end = group["timestamp"].max().ceil(f"{interval_seconds}s")
        history = []
        for window_start in pd.date_range(start, end, freq=f"{interval_seconds}s"):
            window_end = window_start + pd.Timedelta(seconds=interval_seconds)
            window = group[
                (group["timestamp"] >= window_start)
                & (group["timestamp"] < window_end)
            ]
            count = len(window)
            rmssd = (
                compute_rmssd_from_bbi(window["bbi_ms"])
                if count >= min_beats else float("nan")
            )
            baseline = float(pd.Series(history).median()) if history else float("nan")
            ratio = float("nan")
            hrv_class = pd.NA
            if not pd.isna(rmssd) and not pd.isna(baseline) and baseline > 0:
                ratio = rmssd / baseline
                hrv_class = (
                    "Low" if ratio < low_cutoff
                    else "High" if ratio > high_cutoff
                    else "Balanced"
                )
            results.append({
                "user_id": user_id,
                "interval_start": window_start,
                "interval_end": window_end,
                "rmssd": rmssd,
                "count": count,
                "baseline": baseline,
                "ratio": ratio,
                "hrv_class": hrv_class,
            })
            if not pd.isna(rmssd):
                history.append(rmssd)

    return pd.DataFrame(results)
