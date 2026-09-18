"""
Decision engine for MSSD volatility detection

Author: Celia Mercier
"""

import math
from pathlib import Path

import pandas as pd


def calculate_mssd(df: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    """Add the rolling MSSD volatility signal, per participant.

    In:  df with user_id, timestamp, ema columns; window = how many successive
         squared differences to average.
    Out: a copy sorted by (user_id, timestamp) with ema_diff_squared and
         observed_mssd added.

    The rolling window is deliberate: it gives a recency-weighted signal suited
    to real-time triggering. Offline analysis using cumulative MSSD produces
    different values, so JITAILog.observed_mssd is the audit source.
    """
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


def add_within_person_threshold(
    df: pd.DataFrame,
    threshold_quantile: float = 0.80,
) -> pd.DataFrame:
    """Add each participant's own volatility threshold.

    In:  df with user_id, timestamp, observed_mssd; threshold_quantile = which
         quantile of the participant's own history counts as volatile.
    Out: a copy with user_threshold added — expanding quantile over that
         participant's prior rows only, so the current row never sets the bar
         it is judged against. Null until the participant has 3 prior rows.
    """
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
    df: pd.DataFrame,
    threshold_quantile: float = 0.80,
    cooldown_minutes: int = 60,
    max_prompts_per_day: int = 4,
) -> pd.DataFrame:
    """Score every decision point: send a prompt, or say why not.

    In:  df with user_id, timestamp, ema, observed_mssd (the output of
         calculate_mssd); the threshold quantile, the minimum gap between two
         prompts, and the per-day prompt cap.
    Out: a copy with user_threshold, decision_reason and send_prompt added.
         decision_reason is one of "prompt sent", "missing or insufficient EMA
         data", "insufficient within-person history", "below within-person
         threshold", "daily cap reached", "cooldown active"; send_prompt is
         True exactly when the reason is "prompt sent".

    The daily cap counts UTC days while the rest of the study is Eastern — a
    known defect the monitor surfaces rather than works around.
    """
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


def summarize_decisions(df: pd.DataFrame) -> pd.DataFrame:
    """Roll a decision log up to one row per participant.

    In:  df as returned by apply_decision_rules.
    Out: a new frame with user_id, prompts_sent, average_mssd, max_mssd,
         average_threshold, max_threshold.
    """
    summary = df.groupby("user_id").agg(
        prompts_sent=("decision_reason", lambda x: (x == "prompt sent").sum()),
        average_mssd=("observed_mssd", "mean"),
        max_mssd=("observed_mssd", "max"),
        average_threshold=("user_threshold", "mean"),
        max_threshold=("user_threshold", "max"),
    )

    return summary.reset_index()


def validate_prompt_counts_vary(summary_df: pd.DataFrame) -> bool:
    """Guard against a run where every participant got the same prompt count.

    In:  summary_df as returned by summarize_decisions.
    Out: True. Raises AssertionError instead if prompts_sent is identical for
         everyone, which means the counting or the per-person threshold broke.
    """
    unique_counts = summary_df["prompts_sent"].nunique()

    if unique_counts <= 1:
        raise AssertionError(
            "All users received the same prompt count. "
            "This may indicate the prompt-counting logic or per-user threshold is broken."
        )

    return True


def _utc_naive(series: pd.Series) -> pd.Series:
    """Normalize a timestamp column to tz-naive UTC.

    In:  a datetime Series, tz-aware or not.
    Out: the same instants, tz-naive. Naive input is returned unchanged.

    Every ORM timestamp in this project is tz-aware UTC and every Labfront
    export is naive; the window math below can only compare one kind.
    """
    stamps = pd.to_datetime(series)
    if getattr(stamps.dtype, "tz", None) is not None:
        return stamps.dt.tz_convert("UTC").dt.tz_localize(None)
    return stamps


def merge_hr_to_prompts(
    ema_df: pd.DataFrame,
    hr_df: pd.DataFrame,
    tolerance: str = "30min",
) -> pd.DataFrame:
    """Attach each EMA the most recent heart-rate sample that preceded it.

    In:  ema_df with user_id, timestamp; hr_df with user_id, timestamp and
         either hr or bpm; tolerance = how far back a sample may be and still
         count (a pandas offset string).
    Out: a copy of ema_df with an hr column added, null where no sample landed
         inside the tolerance. The caller's timestamp column comes back
         untouched, aware or naive as it went in.

    Raises ValueError if either frame is missing a required column.
    """
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

    
    ema["_merge_ts"] = _utc_naive(ema["timestamp"])
    heart_rate["_merge_ts"] = _utc_naive(heart_rate["timestamp"])
    merged = pd.merge_asof(
        ema.sort_values(["_merge_ts", "user_id"]),
        heart_rate[["user_id", "_merge_ts", "hr"]].sort_values(
            ["_merge_ts", "user_id"]
        ),
        on="_merge_ts",
        by="user_id",
        direction="backward",
        tolerance=pd.Timedelta(tolerance),
    )
    return merged.drop(columns=["_merge_ts"])


def _find_header_row_csv(path: str | Path, marker: str = "timezone,") -> int:
    """Find where a Labfront export's real header row starts.

    In:  path to the CSV; marker = the text the header line begins with.
    Out: that line's 0-based number, to pass as read_csv(skiprows=...).

    Raises ValueError if no line begins with the marker.
    """
    with open(path, "r", encoding="utf-8-sig") as file_handle:
        for row_number, line in enumerate(file_handle):
            if line.startswith(marker):
                return row_number
    raise ValueError("BBI CSV header row not found")


def _participant_id_from_metadata(path: str | Path) -> str | None:
    """Recover the participant ID from a Labfront export's preamble.

    In:  path to the CSV, whose first lines carry a projectId header row and a
         matching values row.
    Out: the participantId value, or None when the preamble does not carry one.
    """
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


def load_bbi_csv(
    path: str | Path,
    confidence_col: str = "confidence",
    min_confidence: int = 1,
    min_bbi_ms: int = 300,
    max_bbi_ms: int = 2000,
) -> pd.DataFrame:
    """Read a Labfront beat-to-beat interval export into a clean frame.

    In:  path to the CSV; the confidence column's name and the minimum
         confidence to keep; the physiological bounds a beat interval must
         fall between, in milliseconds.
    Out: a new frame with user_id, timestamp (tz-naive UTC), bbi_ms and, when
         the export carried one, the confidence column — sorted by
         (user_id, timestamp) with a fresh index. Rows below the confidence
         floor, outside the bounds, or missing any of the three are dropped.

    Raises ValueError if the export has no timestamp column, no bbi column, or
    no way to identify the participant.
    """
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
    bbi = bbi[bbi["bbi"].between(min_bbi_ms, max_bbi_ms)]
    keep_columns = ["user_id", "timestamp", "bbi"]
    if confidence_col in bbi.columns:
        keep_columns.append(confidence_col)
    return bbi[keep_columns].rename(columns={"bbi": "bbi_ms"}).sort_values(
        ["user_id", "timestamp"]
    ).reset_index(drop=True)


def compute_rmssd_from_bbi(bbi_ms_series: pd.Series) -> float:
    """Compute RMSSD over a run of beat-to-beat intervals.

    In:  the intervals in milliseconds, as a Series (a list or array is fine
         too). Non-numeric and missing values are dropped.
    Out: the root mean square of successive differences, in milliseconds, or
         NaN when fewer than two usable intervals remain.
    """
    values = (
        pd.to_numeric(pd.Series(bbi_ms_series), errors="coerce")
        .dropna()
        .to_numpy()
    )
    if len(values) < 2:
        return float("nan")
    return float(math.sqrt(((values[1:] - values[:-1]) ** 2).mean()))


def classify_rmssd(
    rmssd: float,
    baseline: float,
    low_cutoff: float = 0.8,
    high_cutoff: float = 1.2,
) -> tuple[float, str | None]:
    """Place one RMSSD reading against the participant's own baseline.

    In:  the reading and the baseline, both in milliseconds, plus the ratio
         cutoffs that separate the three classes.
    Out: (ratio, class) where class is "Low" below low_cutoff, "High" above
         high_cutoff, and "Balanced" in between — cutoffs exclusive, so
         exactly 0.8 and exactly 1.2 are "Balanced". Returns (NaN, pd.NA) when
         either input is missing or the baseline is not positive; the class is
         pd.NA rather than None so it can sit in a nullable column.

    The cutoffs are provisional and have no PI sign-off, which is why callers
    record this classification and never gate a prompt on it.
    """
    if pd.isna(rmssd) or pd.isna(baseline) or baseline <= 0:
        return float("nan"), pd.NA
    ratio = rmssd / baseline
    if ratio < low_cutoff:
        return ratio, "Low"
    if ratio > high_cutoff:
        return ratio, "High"
    return ratio, "Balanced"


def _baseline_from_history(
    history: list[float],
    baseline_window: int | None,
) -> float:
    """Reduce a participant's prior RMSSD readings to one baseline.

    In:  history = that participant's earlier readings, oldest first;
         baseline_window = how many of the most recent to use, or None for all
         of them.
    Out: their median, or NaN when there is no history yet.
    """
    window = history if baseline_window is None else history[-baseline_window:]
    if not window:
        return float("nan")
    return float(pd.Series(window).median())


def _bbi_by_user(bbi_df: pd.DataFrame) -> dict[object, tuple[pd.Series, pd.Series]]:
    """Index a BBI frame by participant for repeated window lookups.

    In:  bbi_df with user_id, timestamp, bbi_ms.
    Out: {user_id: (timestamps, intervals)}, each pair sorted by time and
         re-indexed from 0 so the two line up positionally. Timestamps are
         tz-naive UTC, which is what makes them searchsorted-able.
    """
    bbi = bbi_df.copy()
    bbi["timestamp"] = _utc_naive(bbi["timestamp"])
    bbi = bbi.sort_values(["user_id", "timestamp"])
    return {
        user_id: (
            group["timestamp"].reset_index(drop=True),
            group["bbi_ms"].reset_index(drop=True),
        )
        for user_id, group in bbi.groupby("user_id", sort=False)
    }


def attach_rmssd_to_decisions(
    decision_df: pd.DataFrame,
    bbi_df: pd.DataFrame,
    window_seconds: int = 300,
    min_beats: int = 5,
    low_cutoff: float = 0.8,
    high_cutoff: float = 1.2,
    baseline_window: int | None = None,
) -> pd.DataFrame:
    """Annotate each decision with the HRV computed from raw BBI before it.

    The offline path, for a Labfront BBI export. See
    attach_rmssd_series_to_decisions for the live one.

    In:  decision_df with user_id, timestamp; bbi_df with user_id, timestamp,
         bbi_ms; window_seconds = how far back of beats to use; min_beats =
         the fewest beats worth computing on; the two ratio cutoffs; and
         baseline_window = how many prior readings the baseline spans (None
         for all of them).
    Out: a copy with rmssd_bbi, rmssd_count, rmssd_baseline, rmssd_ratio and
         hrv_class added. The window is [timestamp - window_seconds,
         timestamp), so a decision never sees a beat recorded at or after it.
         A window with too few beats reports its rmssd_count but leaves the
         rest null, which is how "not measured" stays distinguishable from
         zero.

    Raises ValueError if bbi_df is missing a required column.
    """
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

    by_user = _bbi_by_user(bbi_df)
    ordered = result[["user_id", "timestamp"]].copy()
    ordered["timestamp"] = _utc_naive(ordered["timestamp"])
    history_by_user = {}

    for index, row in ordered.sort_values(["user_id", "timestamp"]).iterrows():
        stamps, values = by_user.get(row["user_id"], (None, None))
        if stamps is None:
            continue

        end = row["timestamp"]
        start = end - pd.Timedelta(seconds=window_seconds)
        left = stamps.searchsorted(start, side="left")
        right = stamps.searchsorted(end, side="left")
        count = int(right - left)
        result.at[index, "rmssd_count"] = count
        if count < min_beats:
            continue

        rmssd = compute_rmssd_from_bbi(values.iloc[left:right])
        result.at[index, "rmssd_bbi"] = rmssd
        history = history_by_user.setdefault(row["user_id"], [])
        baseline = _baseline_from_history(history, baseline_window)
        result.at[index, "rmssd_baseline"] = baseline
        ratio, hrv_class = classify_rmssd(rmssd, baseline, low_cutoff, high_cutoff)
        result.at[index, "rmssd_ratio"] = ratio
        result.at[index, "hrv_class"] = hrv_class
        if not pd.isna(rmssd):
            history.append(rmssd)

    return result


def attach_rmssd_series_to_decisions(
    decision_df: pd.DataFrame,
    hrv_df: pd.DataFrame,
    tolerance: str = "30min",
    baseline_window: int | None = None,
    low_cutoff: float = 0.8,
    high_cutoff: float = 1.2,
) -> pd.DataFrame:
    """Annotate each decision with the most recent RMSSD reading before it.

    The live sibling of attach_rmssd_to_decisions: the database stores the
    already-reduced 5-minute RMSSD series rather than raw BBI, so the window
    math is done and only the lookup and the classification remain.

    In:  decision_df with user_id, timestamp; hrv_df with user_id, timestamp,
         rmssd_ms; tolerance = how stale a reading may be and still count (a
         pandas offset string); baseline_window and the two ratio cutoffs as
         in attach_rmssd_to_decisions.
    Out: a copy with rmssd_ms, rmssd_baseline, rmssd_ratio and hrv_class added
         — note rmssd_ms here, where the BBI path adds rmssd_bbi. All four
         stay null when the participant has no reading inside the tolerance.

    Readings are taken strictly before the decision: hrv_df timestamps are
    interval starts, so one stamped at the decision moment summarizes the five
    minutes after it.

    Raises ValueError if hrv_df is missing a required column.
    """
    required = {"user_id", "timestamp", "rmssd_ms"}
    missing = required - set(hrv_df.columns)
    if missing:
        raise ValueError(f"HRV dataframe missing columns: {sorted(missing)}")

    result = decision_df.copy()
    result["rmssd_ms"] = float("nan")
    result["rmssd_baseline"] = float("nan")
    result["rmssd_ratio"] = float("nan")
    result["hrv_class"] = pd.NA

    hrv = hrv_df.copy()
    hrv["timestamp"] = _utc_naive(hrv["timestamp"])
    hrv = hrv.sort_values(["user_id", "timestamp"])

    by_user = {}
    for user_id, group in hrv.groupby("user_id", sort=False):
        values = pd.to_numeric(group["rmssd_ms"], errors="coerce").reset_index(drop=True)
        if baseline_window is None:
            baselines = values.expanding(min_periods=1).median().shift(1)
        else:
            baselines = values.rolling(
                baseline_window, min_periods=1
            ).median().shift(1)
        by_user[user_id] = (
            group["timestamp"].reset_index(drop=True),
            values,
            baselines,
        )

    ordered = result[["user_id", "timestamp"]].copy()
    ordered["timestamp"] = _utc_naive(ordered["timestamp"])
    max_age = pd.Timedelta(tolerance)

    for index, row in ordered.iterrows():
        stamps, values, baselines = by_user.get(row["user_id"], (None, None, None))
        if stamps is None:
            continue

        # Strictly before: HRVSample.timestamp is the interval start, so a row
        # stamped at the decision moment summarizes the five minutes after it.
        position = stamps.searchsorted(row["timestamp"], side="left") - 1
        if position < 0 or row["timestamp"] - stamps.iloc[position] > max_age:
            continue

        rmssd = values.iloc[position]
        if pd.isna(rmssd):
            continue
        baseline = baselines.iloc[position]
        ratio, hrv_class = classify_rmssd(rmssd, baseline, low_cutoff, high_cutoff)
        result.at[index, "rmssd_ms"] = float(rmssd)
        result.at[index, "rmssd_baseline"] = baseline
        result.at[index, "rmssd_ratio"] = ratio
        result.at[index, "hrv_class"] = hrv_class

    return result


def compute_rmssd_5min_series(
    bbi_df: pd.DataFrame,
    interval_seconds: int = 300,
    min_beats: int = 5,
    low_cutoff: float = 0.8,
    high_cutoff: float = 1.2,
    baseline_window: int | None = None,
) -> pd.DataFrame:
    """Reduce raw BBI to a fixed grid of RMSSD windows, per participant.

    This is what turns a beat-per-row stream into something storable: roughly
    100k rows a day per participant become 288.

    In:  bbi_df with user_id, timestamp, bbi_ms; interval_seconds = the window
         length; min_beats, the two ratio cutoffs and baseline_window as in
         attach_rmssd_to_decisions.
    Out: a new frame with one row per participant per window — user_id,
         interval_start, interval_end, rmssd, count, baseline, ratio,
         hrv_class. Windows run [interval_start, interval_end) and cover the
         participant's whole span, so a window with no beats is a real zero
         count with a null rmssd.

    Raises ValueError if bbi_df is missing a required column.
    """
    required = {"user_id", "timestamp", "bbi_ms"}
    missing = required - set(bbi_df.columns)
    if missing:
        raise ValueError(f"BBI dataframe missing columns: {sorted(missing)}")

    results = []
    for user_id, (stamps, values) in _bbi_by_user(bbi_df).items():
        if stamps.empty:
            continue
        start = stamps.iloc[0].floor(f"{interval_seconds}s")
        end = stamps.iloc[-1].ceil(f"{interval_seconds}s")
        history = []
        for window_start in pd.date_range(start, end, freq=f"{interval_seconds}s"):
            window_end = window_start + pd.Timedelta(seconds=interval_seconds)
            left = stamps.searchsorted(window_start, side="left")
            right = stamps.searchsorted(window_end, side="left")
            count = int(right - left)
            rmssd = (
                compute_rmssd_from_bbi(values.iloc[left:right])
                if count >= min_beats else float("nan")
            )
            baseline = _baseline_from_history(history, baseline_window)
            ratio, hrv_class = classify_rmssd(
                rmssd, baseline, low_cutoff, high_cutoff
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
