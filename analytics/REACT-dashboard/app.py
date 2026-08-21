"""
Installation
------------
From Terminal, inside the REACT-dashboard folder:

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install streamlit pandas
    streamlit run app.py
"""

from pathlib import Path
from typing import Optional
from backend_client import load_backend_health

import pandas as pd
import streamlit as st

st.markdown(
    """
    <style>
    div[data-testid="stMetricValue"] {
        font-size: 1.65rem;
        line-height: 1.2;
    }

    div[data-testid="stMetricLabel"] {
        font-size: 0.85rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.set_page_config(
    page_title="REACT Decision Dashboard",
    page_icon="📊",
    layout="wide",
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_LOG_PATH = DATA_DIR / "decision_log.csv"
DEFAULT_SUMMARY_PATH = DATA_DIR / "decision_summary.csv"
LOCAL_TIMEZONE = "America/New_York"


def to_eastern(series: pd.Series) -> pd.Series:
    """Parse timestamps as UTC and convert them to US Eastern time."""
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_convert(LOCAL_TIMEZONE)

REQUIRED_LOG_COLUMNS = {
    "user_id",
    "timestamp",
    "observed_mssd",
    "user_threshold",
    "send_prompt",
    "decision_reason",
}

REQUIRED_SUMMARY_COLUMNS = {
    "user_id",
    "prompts_sent",
}


def validate_columns(
    dataframe: pd.DataFrame,
    required_columns: set[str],
    file_label: str,
) -> None:
    """Stop the app with a clear error when a CSV is missing columns."""
    missing_columns = required_columns - set(dataframe.columns)

    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        st.error(f"{file_label} is missing required columns: {missing_text}")
        st.stop()


def convert_to_boolean(series: pd.Series) -> pd.Series:
    """Convert common CSV true/false values into actual Boolean values."""
    normalized = series.astype(str).str.strip().str.lower()

    return (
        normalized.map(
            {
                "true": True,
                "false": False,
                "1": True,
                "0": False,
                "yes": True,
                "no": False,
            }
        )
        .fillna(False)
        .astype(bool)
    )


@st.cache_data
def load_default_data(
    log_path: str,
    summary_path: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load and cache the two default CSV files."""
    return pd.read_csv(log_path), pd.read_csv(summary_path)


def clean_data(
    log_df: pd.DataFrame,
    summary_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate columns, normalize types, and sort the data."""
    validate_columns(log_df, REQUIRED_LOG_COLUMNS, "decision_log.csv")
    validate_columns(summary_df, REQUIRED_SUMMARY_COLUMNS, "decision_summary.csv")

    log_df = log_df.copy()
    summary_df = summary_df.copy()

    log_df["timestamp"] = to_eastern(log_df["timestamp"])
    log_df["send_prompt"] = convert_to_boolean(log_df["send_prompt"])

    summary_df["prompts_sent"] = pd.to_numeric(
        summary_df["prompts_sent"],
        errors="coerce",
    ).fillna(0).astype(int)

    log_df = log_df.sort_values(
        ["user_id", "timestamp"],
        na_position="last",
    )

    return log_df, summary_df


def build_consistency_table(
    log_df: pd.DataFrame,
    summary_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compare prompt totals in the summary against totals from the log."""
    log_prompt_counts = (
        log_df.groupby("user_id")["send_prompt"]
        .sum()
        .astype(int)
        .rename("prompts_from_log")
        .reset_index()
    )

    user_table = summary_df.merge(
        log_prompt_counts,
        on="user_id",
        how="left",
    )

    user_table["prompts_from_log"] = (
        user_table["prompts_from_log"].fillna(0).astype(int)
    )

    user_table["count_matches"] = (
        user_table["prompts_sent"] == user_table["prompts_from_log"]
    )

    return user_table

def first_existing_column(
    dataframe: pd.DataFrame,
    candidates: list[str],
) -> Optional[str]:
    """Return the first candidate column present in the dataframe."""
    return next((column for column in candidates if column in dataframe.columns), None)

def build_pipeline_data(log_df: pd.DataFrame):
    """Prepare notification-pipeline timestamps, latency metrics, and health data."""

    pipeline = log_df.copy()


    column_aliases = {
    "decision_made_at": [
        "decision_made_at",
        "decision_timestamp",
    ],
    "push_sent_at": [
        "push_sent_at",
        "push_timestamp",
        "push_sent_timestamp",
        "last_push_timestamp",
    ],
    "device_received_at": [
        "device_received_at",
        "receipt_timestamp",
        "last_receipt_timestamp",
    ],
    "receipt_reported_at": [
        "receipt_reported_at",
        "reciept_reported_at",
    ],
    "last_sync_at": [
        "last_sync_at",
        "last_synced_at",
        "last_sync_timestamp",
        "sync_timestamp",
        "synced_at",
        "last_sync",
    ],
}

    detected_columns = {}

    for standard_name, candidates in column_aliases.items():
        detected_column = first_existing_column(pipeline, candidates)
        detected_columns[standard_name] = detected_column

        if detected_column:
            pipeline[standard_name] = to_eastern(pipeline[detected_column])
        else:
            pipeline[standard_name] = pd.NaT

    # the timestamp design defines these three primary latency stages.
    pipeline["backend_queue_seconds"] = (
        pipeline["push_sent_at"] - pipeline["decision_made_at"]
    ).dt.total_seconds()

    pipeline["push_delivery_seconds"] = (
        pipeline["device_received_at"] - pipeline["push_sent_at"]
    ).dt.total_seconds()

    pipeline["end_to_end_seconds"] = (
        pipeline["device_received_at"] - pipeline["decision_made_at"]
    ).dt.total_seconds()

    # Server-observed fallback when the device and backend clocks disagree.
    pipeline["server_observed_seconds"] = (
        pipeline["receipt_reported_at"] - pipeline["push_sent_at"]
    ).dt.total_seconds()

    latency_columns = [
        "backend_queue_seconds",
        "push_delivery_seconds",
        "end_to_end_seconds",
        "server_observed_seconds",
    ]

    # Negative durations indicate invalid ordering or unsynchronized clocks.
    for column in latency_columns:
        pipeline[column] = pipeline[column].where(pipeline[column] >= 0)


    def determine_delivery_state(row):
        delivery_error = row.get("delivery_error")

        if pd.notna(delivery_error) and str(delivery_error).strip():
            return "Error"

        if pd.notna(row["device_received_at"]):
            return "Healthy"

        if pd.notna(row["push_sent_at"]):
            return "Waiting for device"

        if pd.notna(row["decision_made_at"]):
            return "Waiting for push"

        return "No pipeline activity"

    pipeline["pipeline_state"] = pipeline.apply(
        determine_delivery_state,
        axis=1,
    )

    timestamp_fields_present = any(
        detected_columns[field] is not None
        for field in [
            "decision_made_at",
            "push_sent_at",
            "device_received_at",
            "receipt_reported_at",
        ]
    )

    completed_deliveries = pipeline.dropna(
        subset=["decision_made_at", "device_received_at"]
    ).copy()

    metadata = {
        "timestamp_fields_present": timestamp_fields_present,
        "detected_columns": detected_columns,
        "completed_deliveries": len(completed_deliveries),
    }

    return pipeline, metadata

def format_pipeline_time(value) -> str:
    """Format timestamps compactly for dashboard metric cards."""

    if pd.isna(value):
        return "Not recorded"

    return pd.Timestamp(value).strftime("%b %d, %I:%M:%S %p ET")

def format_latency(value) -> str:
    """Format a latency value in seconds."""

    if pd.isna(value):
        return "Not captured"

    value = float(value)

    if value < 1:
        return f"{value * 1000:.0f} ms"

    return f"{value:.1f} sec"

def build_feasibility_data(log_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Calculate participant- and cohort-level feasibility measures.

    The current synthetic dataset contains scheduled EMA rows and EMA values, so
    response rate and missingness are directly measurable. Response latency is
    calculated when a response timestamp or latency column is supplied. Wear
    gaps are calculated from wearable timestamps when available; otherwise an
    EMA-observation-gap proxy is shown and labeled as such.
    """
    feasibility = log_df.copy()
    feasibility = feasibility.dropna(subset=["user_id", "timestamp"])
    feasibility["date"] = feasibility["timestamp"].dt.date
    feasibility["ema_responded"] = feasibility["ema"].notna() if "ema" in feasibility else False

    latency_column = first_existing_column(
        feasibility,
        ["response_latency_minutes", "latency_minutes", "prompt_to_response_minutes"],
    )
    response_timestamp_column = first_existing_column(
        feasibility,
        ["response_timestamp", "ema_response_timestamp", "completed_at"],
    )

    if latency_column:
        feasibility["latency_minutes"] = pd.to_numeric(
            feasibility[latency_column], errors="coerce"
        )
    elif response_timestamp_column:
        response_times = to_eastern(feasibility[response_timestamp_column])
        feasibility["latency_minutes"] = (
            response_times - feasibility["timestamp"]
        ).dt.total_seconds() / 60
    else:
        feasibility["latency_minutes"] = pd.NA

    # Negative latencies are invalid and should not enter summaries.
    feasibility["latency_minutes"] = pd.to_numeric(
        feasibility["latency_minutes"], errors="coerce"
    ).where(lambda values: values >= 0)

    wearable_timestamp_column = first_existing_column(
        feasibility,
        ["wearable_timestamp", "sensor_timestamp", "device_timestamp"],
    )
    wear_gap_threshold_hours = 2.0

    participant_rows = []
    for user_id, user_data in feasibility.groupby("user_id"):
        user_data = user_data.sort_values("timestamp")
        scheduled = len(user_data)
        completed = int(user_data["ema_responded"].sum())
        response_rate = completed / scheduled if scheduled else 0.0
        missingness = 1.0 - response_rate

        valid_latencies = user_data.loc[
            user_data["ema_responded"], "latency_minutes"
        ].dropna()
        median_latency = valid_latencies.median() if not valid_latencies.empty else pd.NA

        if wearable_timestamp_column:
            wear_times = to_eastern(
                user_data[wearable_timestamp_column]
            ).dropna().sort_values()
            gap_hours = wear_times.diff().dt.total_seconds().div(3600)
            wear_gaps = int((gap_hours > wear_gap_threshold_hours).sum())
            gap_basis = "wearable telemetry"
        else:
            # Current-data proxy: gaps between completed EMA observations.
            observed_times = user_data.loc[user_data["ema_responded"], "timestamp"]
            expected_interval_hours = (
                user_data["timestamp"].sort_values().diff().dt.total_seconds().div(3600).median()
            )
            if pd.isna(expected_interval_hours) or expected_interval_hours <= 0:
                expected_interval_hours = 3.0
            proxy_threshold = max(wear_gap_threshold_hours, expected_interval_hours * 1.5)
            gap_hours = observed_times.diff().dt.total_seconds().div(3600)
            wear_gaps = int((gap_hours > proxy_threshold).sum())
            gap_basis = "EMA observation-gap proxy"

        participant_rows.append(
            {
                "user_id": user_id,
                "scheduled_emas": scheduled,
                "completed_emas": completed,
                "response_rate": response_rate,
                "median_latency_minutes": median_latency,
                "wear_gaps": wear_gaps,
                "missingness": missingness,
            }
        )

    participant_df = pd.DataFrame(participant_rows)

    daily_df = (
        feasibility.groupby("date")
        .agg(
            scheduled_emas=("ema_responded", "size"),
            completed_emas=("ema_responded", "sum"),
        )
        .reset_index()
    )
    daily_df["response_rate"] = (
        daily_df["completed_emas"] / daily_df["scheduled_emas"]
    )
    daily_df["missingness"] = 1.0 - daily_df["response_rate"]

    metadata = {
        "latency_available": feasibility["latency_minutes"].notna().any(),
        "wear_gap_basis": gap_basis if participant_rows else "unavailable",
        "wear_gap_threshold_hours": wear_gap_threshold_hours,
    }
    return participant_df, daily_df, metadata

@st.cache_data(ttl=30)
def load_pipeline_source(data_mode: str) -> tuple[pd.DataFrame, str]:
    """Load the operational source selected by the global dashboard mode."""
    use_mock = data_mode == "Seed"
    return load_backend_health(use_mock=use_mock)

def render_feasibility_view(log_df: pd.DataFrame, data_mode: str) -> None:
    """Render paper-ready feasibility results at cohort and participant levels."""
    st.header("Feasibility Results")
    st.caption(
        "EMA completion, prompt-to-response latency, wear-time gaps, and "
        "missingness at cohort and participant levels."
    )

    if data_mode == "Live":
        st.success("LIVE DATA MODE — only live backend fields are displayed. Seed values are never substituted.")
    else:
        st.info("SEED DATA MODE — synthetic/mock data for testing and demonstration only.")

    participant_df, daily_df, metadata = build_feasibility_data(log_df)

    try:
        pipeline_source_df, pipeline_source_name = load_pipeline_source(data_mode)
        pipeline_df, pipeline_metadata = build_pipeline_data(pipeline_source_df)
        pipeline_load_error = None
    except Exception as exc:
        if data_mode == "Live":
            st.error("Live backend data could not be loaded. Seed data was not substituted.")
            st.code(str(exc))
            return
        pipeline_source_name = "seed decision-log fallback"
        pipeline_df, pipeline_metadata = build_pipeline_data(log_df)
        pipeline_df["data_source"] = pipeline_source_name
        pipeline_df["is_seed_data"] = True
        pipeline_load_error = str(exc)

    if participant_df.empty:
        st.warning("No valid participant timestamps are available for feasibility analysis.")
        return

    is_seed_source = bool(
        "is_seed_data" in pipeline_df.columns
        and pipeline_df["is_seed_data"].fillna(False).astype(bool).any()
    )

    st.subheader("Pipeline health")
    st.caption(f"Pipeline source: {pipeline_source_name}")
    if is_seed_source:
        st.info(
            "Seed data is displayed for layout and testing only. Operational stale "
            "flags are disabled until the live backend is selected."
        )

    if pipeline_load_error:
        st.warning(
            "The backend health source could not be loaded, so pipeline monitoring "
            "is temporarily using the decision log."
        )
        st.code(pipeline_load_error)

    if not pipeline_metadata["timestamp_fields_present"]:
        st.info(
            "Pipeline timestamp fields are not present in the current CSV. "
            "This section will populate automatically when the necessary timestamp "
            "fields are included."
        )
    else:
        last_sync = pipeline_df["last_sync_at"].max()
        last_decision = pipeline_df["decision_made_at"].max()
        last_push = pipeline_df["push_sent_at"].max()
        last_device_receipt = pipeline_df["device_received_at"].max()
        last_backend_receipt = pipeline_df["receipt_reported_at"].max()

        pushed_rows = pipeline_df[
            pipeline_df["push_sent_at"].notna()
        ]

        waiting_count = int(
            (
                pushed_rows["push_sent_at"].notna()
                & pushed_rows["device_received_at"].isna()
            ).sum()
        )

        error_count = int(
            (pipeline_df["pipeline_state"] == "Error").sum()
        )

        if error_count > 0:
            overall_pipeline_status = "Error"
        elif waiting_count > 0:
            overall_pipeline_status = "Waiting"
        elif pd.notna(last_device_receipt):
            overall_pipeline_status = "Healthy"
        elif pd.notna(last_push):
            overall_pipeline_status = "Waiting for receipt"
        elif pd.notna(last_decision):
            overall_pipeline_status = "Waiting for push"
        else:
            overall_pipeline_status = "No activity"

        health_columns = st.columns(5)

        health_columns[0].metric(
            "Last sync",
            format_pipeline_time(last_sync),
        )

        health_columns[1].metric(
            "Last decision",
            format_pipeline_time(last_decision),
        )

        health_columns[2].metric(
            "Last push",
            format_pipeline_time(last_push),
        )

        health_columns[3].metric(
            "Last receipt",
            format_pipeline_time(last_device_receipt),
            help=(
                "The latest device_received_at timestamp reported by "
                "a participant device."
            ),
        )

        health_columns[4].metric(
            "Pipeline status",
            overall_pipeline_status,
            help=(
                f"{waiting_count} delivery or deliveries waiting for a device "
                f"receipt. {error_count} delivery error or errors."
            ),
        )

        st.caption("All pipeline timestamps shown in US Eastern time (America/New_York).")


        if pd.notna(last_backend_receipt):
            st.caption(
                "Latest backend acknowledgment: "
                f"{format_pipeline_time(last_backend_receipt)}"
            )

        # One health row per participant.
        participant_column = (
            "participant_id"
            if "participant_id" in pipeline_df.columns
            else "user_id"
        )

        participant_health = (
            pipeline_df.groupby(participant_column, as_index=False)
            .agg(
                last_sync_at=("last_sync_at", "max"),
                last_push_at=("push_sent_at", "max"),
                last_receipt_at=("device_received_at", "max"),
                last_backend_receipt_at=("receipt_reported_at", "max"),
            )
        )

        # A participant is stale when no sync has been recorded or the most
        # recent sync is more than 24 hours old. Calculate this after grouping
        # so it is based on each participant's latest sync timestamp.
        stale_cutoff = pd.Timestamp.now(tz=LOCAL_TIMEZONE) - pd.Timedelta(hours=24)
        if is_seed_source:
            participant_health["stale"] = pd.NA
        else:
            participant_health["stale"] = (
                participant_health["last_sync_at"].isna()
                | participant_health["last_sync_at"].lt(stale_cutoff)
            )

        def participant_status(row):
            if is_seed_source:
                return "Seed data"
            if row["stale"]:
                return "Stale"
            if pd.notna(row["last_receipt_at"]):
                return "Healthy"
            if pd.notna(row["last_push_at"]):
                return "Waiting for receipt"
            return "No activity"

        participant_health["status"] = participant_health.apply(
            participant_status,
            axis=1,
        )

        participant_health = participant_health.rename(
    columns={
        participant_column: "Participant",
        "last_sync_at": "Last sync",
        "last_push_at": "Last push",
        "last_receipt_at": "Last receipt",
        "last_backend_receipt_at": "Receipt reported",
        "stale": "Stale",
        "status": "Status",
    }
)

        st.markdown("#### Participant health")
        participant_health_display = participant_health.copy()

        timestamp_columns = [
            "Last sync",
            "Last push",
            "Last receipt",
            "Receipt reported",
        ]

        for column in timestamp_columns:
            if column in participant_health_display.columns:
                participant_health_display[column] = participant_health_display[column].apply(
                    lambda value: (
                        pd.Timestamp(value).strftime("%b %d, %I:%M:%S %p ET")
                        if pd.notna(value)
                        else "Not recorded"
                    )
                )

        st.dataframe(
            participant_health_display,
            use_container_width=True,
            hide_index=True,
        )

    st.divider()
    st.subheader("Delivery latency")

    completed_deliveries = pipeline_df.dropna(
        subset=["decision_made_at", "device_received_at"]
    ).copy()

    if completed_deliveries.empty:
        st.info(
            "No completed decision-to-device deliveries have been recorded yet. "
            "The first pipeline test will appear here once both "
            "`decision_made_at` and `device_received_at` are available."
        )
    else:
        end_to_end_values = completed_deliveries[
            "end_to_end_seconds"
        ].dropna()

        backend_queue_values = completed_deliveries[
            "backend_queue_seconds"
        ].dropna()

        push_delivery_values = completed_deliveries[
            "push_delivery_seconds"
        ].dropna()

        latest_delivery = completed_deliveries.sort_values(
            "device_received_at"
        ).iloc[-1]

        latency_columns = st.columns(5)

        latency_columns[0].metric(
            "Latest end-to-end",
            format_latency(latest_delivery["end_to_end_seconds"]),
            help="Time from decision_made_at to device_received_at.",
        )

        latency_columns[1].metric(
            "Median end-to-end",
            format_latency(end_to_end_values.median()),
        )

        latency_columns[2].metric(
            "95th percentile",
            format_latency(end_to_end_values.quantile(0.95)),
        )

        latency_columns[3].metric(
            "Median backend queue",
            format_latency(backend_queue_values.median()),
            help="Time from decision_made_at to push_sent_at.",
        )

        latency_columns[4].metric(
            "Median push delivery",
            format_latency(push_delivery_values.median()),
            help="Time from push_sent_at to device_received_at.",
        )

        st.caption(
            f"{len(completed_deliveries):,} completed pipeline delivery "
            f"{'test' if len(completed_deliveries) == 1 else 'tests'} recorded."
        )

        latency_display = completed_deliveries.copy()

        preferred_columns = [
            participant_column,
            "decision_made_at",
            "push_sent_at",
            "device_received_at",
            "receipt_reported_at",
            "backend_queue_seconds",
            "push_delivery_seconds",
            "end_to_end_seconds",
            "pipeline_state",
        ]

        latency_display = latency_display[
            [
                column
                for column in preferred_columns
                if column in latency_display.columns
            ]
        ].sort_values(
            "device_received_at",
            ascending=False,
        )

        latency_display = latency_display.rename(
            columns={
                participant_column: "Participant",
                "decision_made_at": "Decision made",
                "push_sent_at": "Push sent",
                "device_received_at": "Device received",
                "receipt_reported_at": "Receipt reported",
                "backend_queue_seconds": "Backend queue (sec)",
                "push_delivery_seconds": "Push delivery (sec)",
                "end_to_end_seconds": "End-to-end (sec)",
                "pipeline_state": "Status",
            }
        )

        st.dataframe(
            latency_display,
            use_container_width=True,
            hide_index=True,
        )

    st.divider()

    filter_columns = st.columns([2, 2, 3])
    available_users = sorted(participant_df["user_id"].tolist())
    selected_participant = filter_columns[0].selectbox(
        "Participant detail", ["All participants"] + available_users
    )

    min_date = pd.to_datetime(log_df["timestamp"], errors="coerce").min()
    max_date = pd.to_datetime(log_df["timestamp"], errors="coerce").max()
    filter_columns[1].text_input(
        "Study period",
        value=(
            f"{min_date:%b %d, %Y} – {max_date:%b %d, %Y}"
            if pd.notna(min_date) and pd.notna(max_date)
            else "Unavailable"
        ),
        disabled=True,
    )
    filter_columns[2].info(
        "Metrics update automatically when uploaded CSVs use the supported columns."
    )

    total_scheduled = int(participant_df["scheduled_emas"].sum())
    total_completed = int(participant_df["completed_emas"].sum())
    cohort_response_rate = total_completed / total_scheduled if total_scheduled else 0.0
    cohort_missingness = 1.0 - cohort_response_rate
    all_latencies = pd.to_numeric(
        log_df.get("response_latency_minutes", pd.Series(dtype=float)), errors="coerce"
    ).dropna()

    # Use computed latency values so timestamp-derived latency is also supported.
    feasibility_copy = log_df.copy()
    _, _, _ = participant_df, daily_df, metadata
    participant_latencies = pd.to_numeric(
        participant_df["median_latency_minutes"], errors="coerce"
    ).dropna()
    cohort_latency = participant_latencies.median() if not participant_latencies.empty else pd.NA
    total_wear_gaps = int(participant_df["wear_gaps"].sum())

    metric_columns = st.columns(4)
    metric_columns[0].metric(
        "EMA response rate",
        f"{cohort_response_rate:.1%}",
        help=f"{total_completed:,} completed of {total_scheduled:,} scheduled EMA assessments.",
    )
    metric_columns[1].metric(
        "Median response latency",
        f"{cohort_latency:.0f} min" if pd.notna(cohort_latency) else "Not captured",
        help="Requires a latency field or a response timestamp in the uploaded decision log.",
    )
    metric_columns[2].metric(
        "Wear-time gaps",
        f"{total_wear_gaps:,}",
        help=f"Current basis: {metadata['wear_gap_basis']}.",
    )
    metric_columns[3].metric(
        "Overall missingness",
        f"{cohort_missingness:.1%}",
        help="Share of scheduled EMA rows with no EMA response.",
    )

    if not metadata["latency_available"]:
        st.warning(
            "Prompt-to-response latency is not present in the current synthetic data. "
            "Add `response_latency_minutes` or `response_timestamp` to calculate it."
        )
    if metadata["wear_gap_basis"] != "wearable telemetry":
        st.info(
            "Wearable timestamps are not present, so the current wear-gap value is an "
            "EMA observation-gap proxy. Add `wearable_timestamp`, `sensor_timestamp`, "
            "or `device_timestamp` for true wear-time gaps."
        )

    st.subheader("Feasibility over time")
    trend_df = daily_df.copy()
    trend_df["date"] = pd.to_datetime(trend_df["date"])
    trend_df = trend_df.set_index("date")[["response_rate", "missingness"]]
    trend_df = trend_df.rename(
        columns={"response_rate": "EMA response rate", "missingness": "Missingness"}
    )
    st.line_chart(trend_df, height=300)
    st.caption("Daily proportions range from 0 to 1; hover for exact values.")

    st.subheader("Participant-level results")
    display_df = participant_df.copy()
    display_df["EMA response rate"] = display_df["response_rate"].map(lambda value: f"{value:.1%}")
    display_df["Median latency"] = display_df["median_latency_minutes"].map(
        lambda value: f"{value:.0f} min" if pd.notna(value) else "Not captured"
    )
    display_df["Missingness"] = display_df["missingness"].map(lambda value: f"{value:.1%}")
    display_df = display_df.rename(
        columns={
            "user_id": "Participant",
            "scheduled_emas": "Scheduled EMAs",
            "completed_emas": "Completed EMAs",
            "wear_gaps": "Wear gaps",
        }
    )[
        [
            "Participant",
            "Scheduled EMAs",
            "Completed EMAs",
            "EMA response rate",
            "Median latency",
            "Wear gaps",
            "Missingness",
        ]
    ]
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.download_button(
        label="Download feasibility table as CSV",
        data=display_df.to_csv(index=False).encode("utf-8"),
        file_name="react_feasibility_table.csv",
        mime="text/csv",
        key="download_feasibility_csv",
    )

    detail_user = available_users[0] if selected_participant == "All participants" else selected_participant
    st.subheader(f"Participant detail: {detail_user}")
    user_data = log_df[log_df["user_id"] == detail_user].copy().sort_values("timestamp")
    user_data["date"] = user_data["timestamp"].dt.date
    user_data["EMA completed"] = user_data["ema"].notna().astype(int)
    user_daily = user_data.groupby("date").agg(
        scheduled=("EMA completed", "size"), completed=("EMA completed", "sum")
    )
    user_daily["Response rate"] = user_daily["completed"] / user_daily["scheduled"]
    user_daily["Missingness"] = 1.0 - user_daily["Response rate"]

    detail_left, detail_right = st.columns(2)
    with detail_left:
        st.markdown("#### Response and missingness")
        st.line_chart(user_daily[["Response rate", "Missingness"]], height=280)
    with detail_right:
        st.markdown("#### Observation timeline")
        observation_timeline = user_data.set_index("timestamp")[["EMA completed"]]
        st.bar_chart(observation_timeline, height=280)
        st.caption("1 = completed EMA; 0 = missing EMA.")

    with st.expander("Metric definitions and supported columns"):
        st.markdown(
            """
- **EMA response rate:** completed EMA values divided by scheduled EMA rows.
- **Response latency:** median minutes from prompt to response; supports `response_latency_minutes`, `latency_minutes`, `prompt_to_response_minutes`, or a response timestamp.
- **Wear-time gaps:** gaps above two hours when wearable timestamps exist. The current fallback is explicitly labeled as an EMA observation-gap proxy.
- **Missingness:** scheduled EMA rows without an EMA value, summarized overall and by day.
            """
        )


def render_daily_monitoring_view(log_df: pd.DataFrame, data_mode: str) -> None:
    """Render the single morning screen used to identify pilot issues quickly."""
    st.header("Daily Monitoring")
    st.caption(
        "Morning operational check: data freshness, delivery health, yesterday's "
        "EMA completion, and participants needing attention. Times are Eastern."
    )

    if data_mode == "Live":
        st.success("LIVE DATA MODE — only live backend fields are displayed. Seed values are never substituted.")
    else:
        st.info("SEED DATA MODE — synthetic/mock data for testing and demonstration only.")

    try:
        source_df, source_name = load_pipeline_source(data_mode)
        pipeline_df, _ = build_pipeline_data(source_df)
        load_error = None
    except Exception as exc:
        if data_mode == "Live":
            st.error("Live backend data could not be loaded. Seed data was not substituted.")
            st.code(str(exc))
            return
        source_name = "seed decision-log fallback"
        pipeline_df, _ = build_pipeline_data(log_df)
        pipeline_df["data_source"] = source_name
        pipeline_df["is_seed_data"] = True
        load_error = str(exc)

    is_seed_source = bool(
        "is_seed_data" in pipeline_df.columns
        and pipeline_df["is_seed_data"].fillna(False).astype(bool).any()
    )
    if is_seed_source:
        st.info(
            "SEED DATA MODE — use this screen to test layout only. Stale counts and "
            "operational alerts are intentionally disabled."
        )
    else:
        st.success(f"LIVE DATA MODE — source: {source_name}")

    if load_error:
        st.warning("The live monitoring source could not be loaded.")
        st.code(load_error)

    participant_column = "participant_id" if "participant_id" in pipeline_df.columns else "user_id"
    now_et = pd.Timestamp.now(tz=LOCAL_TIMEZONE)
    yesterday = (now_et - pd.Timedelta(days=1)).date()
    stale_cutoff = now_et - pd.Timedelta(hours=24)

    health = (
        pipeline_df.groupby(participant_column, as_index=False)
        .agg(
            last_sync=("last_sync_at", "max"),
            last_push=("push_sent_at", "max"),
            last_receipt=("device_received_at", "max"),
            last_backend_receipt=("receipt_reported_at", "max"),
        )
    ) if participant_column in pipeline_df.columns else pd.DataFrame()

    if not health.empty:
        health["stale"] = False if is_seed_source else (
            health["last_sync"].isna() | health["last_sync"].lt(stale_cutoff)
        )

        # Determine waiting status from each participant's latest delivery event.
        # This prevents an older receipt from masking a newer unreceived push.
        latest_events = pipeline_df.copy()
        latest_events["_event_sort_time"] = latest_events["push_sent_at"].fillna(
            latest_events["decision_made_at"]
        )
        latest_events = (
            latest_events.sort_values(
                [participant_column, "_event_sort_time"],
                ascending=[True, True],
                na_position="first",
            )
            .groupby(participant_column, as_index=False)
            .tail(1)
        )
        latest_events = latest_events[[
            participant_column, "push_sent_at", "device_received_at"
        ]].copy()
        latest_events["waiting"] = (
            latest_events["push_sent_at"].notna()
            & latest_events["device_received_at"].isna()
        )
        health = health.merge(
            latest_events[[participant_column, "waiting"]],
            on=participant_column,
            how="left",
        )
        health["waiting"] = health["waiting"].fillna(False).astype(bool)
    active_participants = int(health[participant_column].nunique()) if not health.empty else 0
    stale_count = 0 if is_seed_source or health.empty else int(health["stale"].sum())
    waiting_count = int(health["waiting"].sum()) if not health.empty else 0
    error_count = int((pipeline_df["pipeline_state"] == "Error").sum())

    if data_mode == "Seed":
        feasibility = log_df.dropna(subset=["user_id", "timestamp"]).copy()
        feasibility["local_date"] = feasibility["timestamp"].dt.date
        feasibility["ema_completed"] = feasibility["ema"].notna() if "ema" in feasibility else False
        yesterday_rows = feasibility[feasibility["local_date"] == yesterday]
        yesterday_scheduled = len(yesterday_rows)
        yesterday_completed = int(yesterday_rows["ema_completed"].sum()) if yesterday_scheduled else 0
        yesterday_rate = yesterday_completed / yesterday_scheduled if yesterday_scheduled else None
    else:
        yesterday_rows = pd.DataFrame()
        yesterday_scheduled = 0
        yesterday_completed = 0
        yesterday_rate = None

    metrics = st.columns(5)
    metrics[0].metric("Participants", active_participants)
    metrics[1].metric("Stale >24h", "Disabled" if is_seed_source else stale_count)
    metrics[2].metric("Waiting receipts", waiting_count)
    metrics[3].metric("Pipeline errors", error_count)
    metrics[4].metric(
        "Yesterday EMA",
        (f"{yesterday_rate:.0%}" if yesterday_rate is not None else "No rows")
        if data_mode == "Seed" else "Not live yet",
        help=(
            f"{yesterday_completed} completed of {yesterday_scheduled} scheduled on {yesterday:%b %d}."
            if data_mode == "Seed"
            else "EMA completion is not yet supplied by the live backend, so seed EMA data is not shown in Live mode."
        )
    )

    st.markdown("#### Needs attention")
    attention_rows = []
    if not health.empty:
        for _, row in health.iterrows():
            reasons = []
            if not is_seed_source and row["stale"]:
                reasons.append("No sync in 24h")
            if row["waiting"]:
                reasons.append("Push waiting for receipt")
            if reasons:
                attention_rows.append({
                    "Participant": row[participant_column],
                    "Reason": "; ".join(reasons),
                    "Last sync": row["last_sync"],
                    "Last push": row["last_push"],
                    "Last receipt": row["last_receipt"],
                })

    participant_yesterday = (
        yesterday_rows.groupby("user_id")
        .agg(Scheduled=("ema_completed", "size"), Completed=("ema_completed", "sum"))
        .reset_index()
    ) if data_mode == "Seed" and not yesterday_rows.empty else pd.DataFrame()
    if not participant_yesterday.empty:
        participant_yesterday["Rate"] = participant_yesterday["Completed"] / participant_yesterday["Scheduled"]
        for _, row in participant_yesterday[participant_yesterday["Rate"] < 0.5].iterrows():
            attention_rows.append({
                "Participant": row["user_id"],
                "Reason": f"Yesterday EMA completion {row['Rate']:.0%}",
                "Last sync": pd.NaT,
                "Last push": pd.NaT,
                "Last receipt": pd.NaT,
            })

    attention_df = pd.DataFrame(attention_rows)

    if attention_df.empty:
        st.success("No participants currently meet the dashboard's attention rules.")
    else:
        attention_display = attention_df.copy()

        for column in ["Last sync", "Last push", "Last receipt"]:
            if column in attention_display.columns:
                attention_display[column] = attention_display[column].apply(
                    lambda value: (
                        pd.Timestamp(value).strftime("%b %d, %I:%M:%S %p ET")
                        if pd.notna(value)
                        else "Not recorded"
                    )
                )

        st.dataframe(
            attention_display,
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("#### Latest operational activity")

    recent = pipeline_df.sort_values(
        "decision_made_at",
        ascending=False,
        na_position="last",
    ).head(20)

    recent_columns = [
        c
        for c in [
            participant_column,
            "decision_made_at",
            "push_sent_at",
            "device_received_at",
            "pipeline_state",
        ]
        if c in recent.columns
    ]

    recent_display = recent[recent_columns].copy()

    for column in [
        "decision_made_at",
        "push_sent_at",
        "device_received_at",
    ]:
        if column in recent_display.columns:
            recent_display[column] = recent_display[column].apply(
                lambda value: (
                    pd.Timestamp(value).strftime("%b %d, %I:%M:%S %p ET")
                    if pd.notna(value)
                    else "Not recorded"
                )
            )

    recent_display = recent_display.rename(
        columns={
            participant_column: "Participant",
            "decision_made_at": "Decision made",
            "push_sent_at": "Push sent",
            "device_received_at": "Device received",
            "pipeline_state": "Status",
        }
    )

    st.dataframe(
        recent_display,
        use_container_width=True,
        hide_index=True,
    )



def render_participant_detail_view(log_df: pd.DataFrame, summary_df: pd.DataFrame, data_mode: str) -> None:
    """Render a pre-visit status screen for one participant."""
    st.header("Participant Detail")
    st.caption(
        "Pre-visit check for one participant: current device/backend health, "
        "EMA completion, recent prompts, and decision history. Times are Eastern."
    )

    if data_mode == "Live":
        st.success("LIVE DATA MODE — only live backend fields are displayed. Seed values are never substituted.")
    else:
        st.info("SEED DATA MODE — synthetic/mock data for testing and demonstration only.")

    try:
        source_df, source_name = load_pipeline_source(data_mode)
        pipeline_df, _ = build_pipeline_data(source_df)
        load_error = None
    except Exception as exc:
        if data_mode == "Live":
            st.error("Live backend data could not be loaded. Seed data was not substituted.")
            st.code(str(exc))
            return
        source_name = "seed decision-log fallback"
        pipeline_df, _ = build_pipeline_data(log_df)
        pipeline_df["data_source"] = source_name
        pipeline_df["is_seed_data"] = True
        load_error = str(exc)

    is_seed_source = bool(
        "is_seed_data" in pipeline_df.columns
        and pipeline_df["is_seed_data"].fillna(False).astype(bool).any()
    )

    if load_error:
        st.warning(
            "The live backend could not be loaded, so operational fields are using "
            "seed/fallback data."
        )
        st.code(load_error)

    # Build a participant selector from backend identifiers first.  Keep user_id
    # attached when the backend supplies it so analysis/EMA rows can be linked.
    participant_options = []
    seen_option_keys = set()

    if not pipeline_df.empty:
        if "participant_id" in pipeline_df.columns:
            pair_columns = ["participant_id"]
            if "user_id" in pipeline_df.columns:
                pair_columns.append("user_id")

            pairs = pipeline_df[pair_columns].drop_duplicates()
            for _, row in pairs.iterrows():
                participant_id = row.get("participant_id")
                user_id = row.get("user_id") if "user_id" in pair_columns else pd.NA

                if pd.isna(participant_id):
                    continue

                key = ("participant", str(participant_id))
                if key in seen_option_keys:
                    continue
                seen_option_keys.add(key)

                label = str(participant_id)
                if pd.notna(user_id):
                    label += f" (user {user_id})"

                participant_options.append(
                    {
                        "label": label,
                        "participant_id": participant_id,
                        "user_id": user_id,
                    }
                )
        elif "user_id" in pipeline_df.columns:
            for user_id in pipeline_df["user_id"].dropna().drop_duplicates().tolist():
                key = ("user", str(user_id))
                if key in seen_option_keys:
                    continue
                seen_option_keys.add(key)
                participant_options.append(
                    {
                        "label": f"User {user_id}",
                        "participant_id": pd.NA,
                        "user_id": user_id,
                    }
                )

    # Decision-log-only participants are seed/analysis records and must never
    # appear as live participants.
    if data_mode == "Seed":
        represented_users = {
            str(option["user_id"])
            for option in participant_options
            if pd.notna(option["user_id"])
        }
        for user_id in log_df["user_id"].dropna().drop_duplicates().tolist():
            if str(user_id) in represented_users:
                continue
            participant_options.append(
                {
                    "label": f"User {user_id} (analysis only)",
                    "participant_id": pd.NA,
                    "user_id": user_id,
                }
            )

    if not participant_options:
        st.warning("No participants are available in the current data sources.")
        return

    participant_options = sorted(
        participant_options,
        key=lambda option: option["label"].lower(),
    )
    selected_label = st.selectbox(
        "Choose participant",
        [option["label"] for option in participant_options],
    )
    selected = next(
        option for option in participant_options if option["label"] == selected_label
    )
    selected_participant_id = selected["participant_id"]
    selected_user_id = selected["user_id"]

    if is_seed_source:
        st.info(
            "SEED DATA MODE — operational stale status is disabled. This page can "
            "still be used to test the participant-detail workflow."
        )
    else:
        st.success(f"LIVE OPERATIONAL DATA — source: {source_name}")

    # Filter backend/pipeline rows for the selected participant.
    participant_pipeline = pipeline_df.iloc[0:0].copy()
    if pd.notna(selected_participant_id) and "participant_id" in pipeline_df.columns:
        participant_pipeline = pipeline_df[
            pipeline_df["participant_id"].astype(str) == str(selected_participant_id)
        ].copy()
    elif pd.notna(selected_user_id) and "user_id" in pipeline_df.columns:
        participant_pipeline = pipeline_df[
            pipeline_df["user_id"].astype(str) == str(selected_user_id)
        ].copy()

    # If a selected backend row did not carry user_id, recover it when possible.
    if pd.isna(selected_user_id) and not participant_pipeline.empty and "user_id" in participant_pipeline.columns:
        linked_users = participant_pipeline["user_id"].dropna()
        if not linked_users.empty:
            selected_user_id = linked_users.iloc[0]

    participant_log = log_df.iloc[0:0].copy()
    if data_mode == "Seed" and pd.notna(selected_user_id):
        participant_log = log_df[
            log_df["user_id"].astype(str) == str(selected_user_id)
        ].copy().sort_values("timestamp")

    now_et = pd.Timestamp.now(tz=LOCAL_TIMEZONE)
    today = now_et.date()
    yesterday = (now_et - pd.Timedelta(days=1)).date()
    stale_cutoff = now_et - pd.Timedelta(hours=24)

    last_sync = participant_pipeline["last_sync_at"].max() if "last_sync_at" in participant_pipeline else pd.NaT
    last_push = participant_pipeline["push_sent_at"].max() if "push_sent_at" in participant_pipeline else pd.NaT
    last_receipt = participant_pipeline["device_received_at"].max() if "device_received_at" in participant_pipeline else pd.NaT
    last_backend_receipt = participant_pipeline["receipt_reported_at"].max() if "receipt_reported_at" in participant_pipeline else pd.NaT

    stale = False if is_seed_source else (pd.isna(last_sync) or last_sync < stale_cutoff)

    # Evaluate delivery state from the participant's latest delivery event, not
    # from independently aggregated max(push) and max(receipt) timestamps.
    latest_delivery = None
    latest_event_waiting = False
    latest_event_error = False
    if not participant_pipeline.empty:
        event_sort_time = participant_pipeline["push_sent_at"].copy()
        event_sort_time = event_sort_time.fillna(participant_pipeline["decision_made_at"])
        event_rows = participant_pipeline.assign(_event_sort_time=event_sort_time).sort_values(
            "_event_sort_time", ascending=False, na_position="last"
        )
        latest_delivery = event_rows.iloc[0]
        latest_event_waiting = (
            pd.notna(latest_delivery.get("push_sent_at"))
            and pd.isna(latest_delivery.get("device_received_at"))
        )
        latest_event_error = latest_delivery.get("pipeline_state") == "Error"

    if latest_event_error:
        overall_status = "Needs attention"
    elif stale:
        overall_status = "Needs attention"
    elif latest_event_waiting:
        overall_status = "Waiting for receipt"
    elif pd.notna(last_sync) or pd.notna(last_receipt):
        overall_status = "Healthy"
    elif is_seed_source:
        overall_status = "Seed data"
    else:
        overall_status = "No activity"

    st.subheader(f"Status: {overall_status}")

    status_columns = st.columns(4)
    status_columns[0].metric("Last sync", format_pipeline_time(last_sync))
    status_columns[1].metric("Last push", format_pipeline_time(last_push))
    status_columns[2].metric("Last receipt", format_pipeline_time(last_receipt))
    status_columns[3].metric(
        "Operational status",
        overall_status,
        help="Stale is based on the latest live sync. Waiting is based on the latest delivery event.",
    )
    st.caption("All operational timestamps shown in US Eastern time (America/New_York).")

    # Calculate participant EMA and decision metrics from the analysis/decision log.
    today_rate = None
    yesterday_rate = None
    overall_response_rate = None
    prompts_sent = 0

    if not participant_log.empty:
        participant_log["local_date"] = participant_log["timestamp"].dt.date
        participant_log["ema_completed"] = (
            participant_log["ema"].notna() if "ema" in participant_log.columns else False
        )

        def completion_rate_for(day):
            rows = participant_log[participant_log["local_date"] == day]
            if rows.empty:
                return None, 0, 0
            scheduled = len(rows)
            completed = int(rows["ema_completed"].sum())
            return completed / scheduled, completed, scheduled

        today_rate, today_completed, today_scheduled = completion_rate_for(today)
        yesterday_rate, yesterday_completed, yesterday_scheduled = completion_rate_for(yesterday)
        overall_response_rate = float(participant_log["ema_completed"].mean())
        prompts_sent = int(participant_log["send_prompt"].sum())
    else:
        today_completed = today_scheduled = 0
        yesterday_completed = yesterday_scheduled = 0

    study_columns = st.columns(4)
    if data_mode == "Live":
        study_columns[0].metric("Today's EMA", "Not live yet")
        study_columns[1].metric("Yesterday's EMA", "Not live yet")
        study_columns[2].metric("Overall EMA response", "Not live yet")
        study_columns[3].metric("Prompts sent", "Not live yet")
        st.caption(
            "EMA and decision-engine fields are not yet supplied by the live backend. "
            "Seed values are intentionally hidden in Live mode."
        )
    else:
        study_columns[0].metric(
            "Today's EMA",
            f"{today_rate:.0%}" if today_rate is not None else "No rows",
            help=f"{today_completed} completed of {today_scheduled} scheduled today.",
        )
        study_columns[1].metric(
            "Yesterday's EMA",
            f"{yesterday_rate:.0%}" if yesterday_rate is not None else "No rows",
            help=f"{yesterday_completed} completed of {yesterday_scheduled} scheduled yesterday.",
        )
        study_columns[2].metric(
            "Overall EMA response",
            f"{overall_response_rate:.0%}" if overall_response_rate is not None else "Not available",
        )
        study_columns[3].metric("Prompts sent", prompts_sent if not participant_log.empty else "Not available")

    st.markdown("#### Current issues")
    issues = []
    if not is_seed_source and stale:
        issues.append("No live device sync has been recorded in the past 24 hours.")
    if latest_event_waiting:
        issues.append("The latest push is still waiting for a device receipt.")
    if latest_event_error:
        issues.append("The latest delivery event is marked as an error.")
    if yesterday_rate is not None and yesterday_rate < 0.5:
        issues.append(f"Yesterday's EMA completion was {yesterday_rate:.0%}.")

    if issues:
        for issue in issues:
            st.warning(issue)
    else:
        st.success("No current issues meet the dashboard's attention rules.")

    st.divider()
    st.subheader("Recent operational activity")

    if participant_pipeline.empty:
        st.info("No backend operational records are available for this participant.")
    else:
        recent = participant_pipeline.copy()
        recent["_event_sort_time"] = recent["decision_made_at"].fillna(recent["push_sent_at"])
        recent = recent.sort_values("_event_sort_time", ascending=False, na_position="last").head(20)

        recent_columns = [
            column
            for column in [
                "decision_made_at",
                "push_sent_at",
                "device_received_at",
                "receipt_reported_at",
                "end_to_end_seconds",
                "pipeline_state",
            ]
            if column in recent.columns
        ]
        recent_display = recent[recent_columns].copy()

        for column in [
            "decision_made_at",
            "push_sent_at",
            "device_received_at",
            "receipt_reported_at",
        ]:
            if column in recent_display.columns:
                recent_display[column] = recent_display[column].apply(format_pipeline_time)

        if "end_to_end_seconds" in recent_display.columns:
            recent_display["end_to_end_seconds"] = recent_display["end_to_end_seconds"].apply(format_latency)

        recent_display = recent_display.rename(
            columns={
                "decision_made_at": "Decision made",
                "push_sent_at": "Push sent",
                "device_received_at": "Device received",
                "receipt_reported_at": "Receipt reported",
                "end_to_end_seconds": "End-to-end",
                "pipeline_state": "Status",
            }
        )
        st.dataframe(recent_display, use_container_width=True, hide_index=True)

        if pd.notna(last_backend_receipt):
            st.caption(f"Latest backend acknowledgment: {format_pipeline_time(last_backend_receipt)}")

    st.divider()
    st.subheader("Participant study history")

    if participant_log.empty:
        if data_mode == "Live":
            st.info(
                "Live decision/EMA history is not connected yet. Seed history is "
                "intentionally hidden while Live mode is selected."
            )
        else:
            st.info(
                "No decision/EMA rows are linked to this participant in the current "
                "seed analysis source. Operational status above is still valid."
            )
    else:
        if "decision_reason" in participant_log.columns and not participant_log["decision_reason"].dropna().empty:
            most_common_reason = participant_log["decision_reason"].mode().iloc[0]
        else:
            most_common_reason = "No reason recorded"

        history_metrics = st.columns(3)
        history_metrics[0].metric("Decision records", len(participant_log))
        history_metrics[1].metric("Most common reason", most_common_reason)
        history_metrics[2].metric("Prompts in log", prompts_sent)

        preferred_history_columns = [
            "timestamp",
            "ema",
            "observed_mssd",
            "user_threshold",
            "send_prompt",
            "decision_reason",
        ]
        history_columns = [
            column for column in preferred_history_columns if column in participant_log.columns
        ]
        history_display = participant_log[history_columns].sort_values(
            "timestamp", ascending=False
        ).head(50).copy()
        if "timestamp" in history_display.columns:
            history_display["timestamp"] = history_display["timestamp"].apply(format_pipeline_time)

        st.dataframe(history_display, use_container_width=True, hide_index=True)

def render_decision_view(log_df: pd.DataFrame, summary_df: pd.DataFrame, user_table: pd.DataFrame) -> None:
    # Header
    st.header("Decision Engine")
    st.caption("Prompt totals and decision explanations from the decision engine.")


    # Top-level metrics
    total_users = summary_df["user_id"].nunique()
    total_prompts = int(log_df["send_prompt"].sum())
    total_records = len(log_df)
    count_mismatches = int((~user_table["count_matches"]).sum())

    metric_columns = st.columns(4)
    metric_columns[0].metric("Users", total_users)
    metric_columns[1].metric("Prompts sent", total_prompts)
    metric_columns[2].metric("Decision records", total_records)
    metric_columns[3].metric("Count mismatches", count_mismatches)

    if count_mismatches == 0:
        st.success("Prompt totals in the summary match the detailed decision log.")
    else:
        st.warning("Some prompt totals in the summary do not match the log.")


    # Cohort-level views
    st.divider()
    left_column, right_column = st.columns(2)

    with left_column:
        st.subheader("Prompt count by user")

        prompt_distribution = (
        user_table["prompts_sent"]
        .value_counts()
        .sort_index()
        .rename_axis("prompts_sent")
        .reset_index(name="number_of_users")
        )

        st.bar_chart(
            prompt_distribution.set_index("prompts_sent"),
            height=320,
        )

        st.caption(
            "This chart shows how many users received each number of prompts."
        )

        st.dataframe(
            user_table.sort_values(
                ["prompts_sent", "user_id"],
                ascending=[False, True],
            ),
            use_container_width=True,
            hide_index=True,
        )

    with right_column:
        st.subheader("Why decisions were made")

        reason_counts = (
            log_df["decision_reason"]
            .fillna("missing reason")
            .value_counts()
            .rename_axis("decision_reason")
            .reset_index(name="count")
        )

        st.bar_chart(
            reason_counts.set_index("decision_reason"),
            horizontal=True,
            height=420,
        )

        st.dataframe(
            reason_counts,
            use_container_width=True,
            hide_index=True,
        )


    # Per-user detail view
    st.divider()
    st.subheader("Per-user details")

    available_users = sorted(summary_df["user_id"].dropna().unique().tolist())

    if not available_users:
        st.warning("No users were found in decision_summary.csv.")
        st.stop()

    selected_user = st.selectbox("Choose a user", available_users)

    user_log = log_df[log_df["user_id"] == selected_user].copy()
    user_summary_rows = summary_df[summary_df["user_id"] == selected_user]

    if user_summary_rows.empty:
        st.error("The selected user is missing from decision_summary.csv.")
        st.stop()

    user_summary = user_summary_rows.iloc[0]

    if user_log["decision_reason"].dropna().empty:
        most_common_reason = "No reason recorded"
    else:
        most_common_reason = user_log["decision_reason"].mode().iloc[0]

    user_metric_columns = st.columns(3)
    user_metric_columns[0].metric(
        "Prompts sent",
        int(user_summary["prompts_sent"]),
    )
    user_metric_columns[1].metric("Decision records", len(user_log))
    user_metric_columns[2].metric("Most common reason", most_common_reason)


    detail_left, detail_right = st.columns(2)

    with detail_left:
        st.markdown("### Reason breakdown")

        user_reason_counts = (
            user_log["decision_reason"]
            .fillna("missing reason")
            .value_counts()
            .rename_axis("decision_reason")
            .reset_index(name="count")
        )

        st.bar_chart(
            user_reason_counts.set_index("decision_reason"),
            horizontal=True,
        )

        st.dataframe(
            user_reason_counts,
            use_container_width=True,
            hide_index=True,
        )

    with detail_right:
        st.markdown("### MSSD versus threshold")

        timeline_df = (
            user_log[["timestamp", "observed_mssd", "user_threshold"]]
            .dropna(subset=["timestamp"])
            .set_index("timestamp")
        )

        if timeline_df.empty:
            st.info("No valid timestamps are available for this user.")
        else:
            st.line_chart(timeline_df, height=320)

        st.caption(
            "Observed MSSD represents measured volatility. The threshold is the "
            "personalized comparison value used by the decision engine."
        )


    # Detailed decision history
    st.markdown("### Decision history")

    preferred_history_columns = [
        "timestamp",
        "ema",
        "observed_mssd",
        "user_threshold",
        "send_prompt",
        "decision_reason",
    ]

    history_columns = [
        column for column in preferred_history_columns if column in user_log.columns
    ]

    st.dataframe(
        user_log[history_columns],
        use_container_width=True,
        hide_index=True,
    )


    # Optional raw data
    with st.expander("Show raw input data"):
        st.markdown("#### decision_log.csv")
        st.dataframe(log_df, use_container_width=True, hide_index=True)

        st.markdown("#### decision_summary.csv")
        st.dataframe(summary_df, use_container_width=True, hide_index=True)

# Sidebar data selection
st.sidebar.header("Data mode")

data_mode = st.sidebar.radio(
    "Choose dashboard source",
    ["Live", "Seed"],
    index=0,
    help=(
        "Live uses only the backend. Seed uses bundled/mock data for testing. "
        "The dashboard never substitutes seed values while Live mode is selected."
    ),
)

if data_mode == "Live":
    st.sidebar.success("LIVE DATA")
else:
    st.sidebar.info("SEED DATA — DEMO ONLY")

use_uploaded_files = False
if data_mode == "Seed":
    use_uploaded_files = st.sidebar.toggle(
        "Upload different seed CSV files",
        value=False,
        help="Use this to inspect a newer synthetic/analysis run.",
    )

if use_uploaded_files:
    uploaded_log = st.sidebar.file_uploader("Upload decision_log.csv", type="csv")
    uploaded_summary = st.sidebar.file_uploader("Upload decision_summary.csv", type="csv")
    if uploaded_log is None or uploaded_summary is None:
        st.info("Upload both CSV files to continue.")
        st.stop()
    raw_log_df = pd.read_csv(uploaded_log)
    raw_summary_df = pd.read_csv(uploaded_summary)
else:
    if not DEFAULT_LOG_PATH.exists() or not DEFAULT_SUMMARY_PATH.exists():
        st.error(
            "The bundled seed CSV files were not found. Create a folder named "
            "`data` beside `app.py`, then place `decision_log.csv` and "
            "`decision_summary.csv` inside it."
        )
        st.stop()
    raw_log_df, raw_summary_df = load_default_data(
        str(DEFAULT_LOG_PATH),
        str(DEFAULT_SUMMARY_PATH),
    )

decision_data_source = (
    "uploaded seed analysis files" if use_uploaded_files else "bundled seed CSV files"
)

# Seed analysis files remain loaded so Seed mode can use them, but Live mode
# renderers must never surface them as live values.
log_df, summary_df = clean_data(raw_log_df, raw_summary_df)
user_table = build_consistency_table(log_df, summary_df)



# Page navigation
st.sidebar.divider()
selected_view = st.sidebar.radio(
    "Dashboard view",
    ["Daily monitoring", "Participant detail", "Decision engine", "Feasibility"],
    index=0,
)

st.title("REACT Decision Dashboard")
if data_mode == "Live":
    st.caption("GLOBAL MODE: LIVE — backend data only. Seed values are never substituted.")
else:
    st.caption(f"GLOBAL MODE: SEED — {decision_data_source}; mock operational data. Demo/testing only.")

if selected_view == "Daily monitoring":
    render_daily_monitoring_view(log_df, data_mode)
elif selected_view == "Participant detail":
    render_participant_detail_view(log_df, summary_df, data_mode)
elif selected_view == "Feasibility":
    if data_mode == "Live":
        st.header("Feasibility Results")
        st.info(
            "Live feasibility/EMA data is not connected yet. Switch to Seed mode to view "
            "the synthetic feasibility analysis. No seed values are shown in Live mode."
        )
    else:
        render_feasibility_view(log_df, data_mode)
else:
    if data_mode == "Live":
        st.header("Decision Engine")
        st.info(
            "Live decision-engine output is not connected yet. Switch to Seed mode to view "
            "the synthetic decision log and summary. No seed values are shown in Live mode."
        )
    else:
        render_decision_view(log_df, summary_df, user_table)