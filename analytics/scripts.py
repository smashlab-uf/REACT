from __future__ import annotations

import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import scipy.stats as stats
import matplotlib.pyplot as plt
import seaborn as sns


# DATA LOADING
# Source tables: app_user, app_ema, app_jitailog, app_heartratesample,
#               app_stresssample, app_engagementlog, app_wearabledevice
# Column names follow analysis-resources/SCHEMA.md.



def load_ema(
    user_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """
    Purpose:
        Load EMA prompt records from app_ema into a DataFrame, filtered by
        participant and/or date range.

    Inputs:
        user_id    - int or None. If None, returns records for all participants.
        start_date - datetime.date or None. Inclusive lower bound on sent_at.
        end_date   - datetime.date or None. Inclusive upper bound on sent_at.

    Outputs:
        pd.DataFrame with columns matching app_ema:
            id, user_id, prompt_id, sent_at, responded_at, status, ema_type,
            source_jitai_log_id, outcome_window_start, outcome_window_end,
            expires_at, mood, stress, energy.
        One row per EMA prompt. Timestamps are timezone-aware (UTC).
    """


def load_jitai_log(
    user_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """
    Purpose:
        Load JITAI decision-point records from app_jitailog into a DataFrame.
        Covers all decision columns, trigger context, and delivery funnel columns.

    Inputs:
        user_id    - int or None. If None, returns records for all participants.
        start_date - datetime.date or None. Inclusive lower bound on triggered_at.
        end_date   - datetime.date or None. Inclusive upper bound on triggered_at.

    Outputs:
        pd.DataFrame with columns matching app_jitailog:
            id, user_id, prompt_id, triggered_at, decision_point_id,
            decision_made_at, trigger_reason, trigger_signal, observed_mssd,
            randomization_probability, randomization_draw, send_prompt,
            eligible_prompt_ids, hr_at_trigger, stress_at_trigger, ema_id,
            ema_mood, ema_stress, ema_energy, status, delivery_status,
            push_sent_at, device_received_at, receipt_reported_at,
            receipt_platform, receipt_app_state, delivery_error.
        One row per decision point. Timestamps are timezone-aware (UTC).
    """


def load_heart_rate(
    user_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """
    Purpose:
        Load heart-rate samples from app_heartratesample into a DataFrame.
        Cadence is ~15 seconds from Labfront; subject to 2-5 min sync lag.

    Inputs:
        user_id    - int or None. If None, returns records for all participants.
        start_date - datetime.date or None. Inclusive lower bound on timestamp.
        end_date   - datetime.date or None. Inclusive upper bound on timestamp.

    Outputs:
        pd.DataFrame with columns: id, user_id, timestamp, bpm, source.
        bpm = 0 or NaN is treated as inferred non-wear (no explicit wear flag).
        Timestamps are timezone-aware (UTC).
    """


def load_stress_samples(
    user_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """
    Purpose:
        Load Garmin stress-score samples from app_stresssample into a DataFrame.
        Garmin stress (0-100) is a black-box HRV-derived metric, NOT a function
        of raw heart rate.

    Inputs:
        user_id    - int or None. If None, returns records for all participants.
        start_date - datetime.date or None. Inclusive lower bound on timestamp.
        end_date   - datetime.date or None. Inclusive upper bound on timestamp.

    Outputs:
        pd.DataFrame with columns: id, user_id, timestamp, stress_score, source.
        Timestamps are timezone-aware (UTC).
    """


def load_engagement_log(
    user_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """
    Purpose:
        Load user engagement events (EMA opened/dismissed/completed,
        notification tapped/dismissed) from app_engagementlog.

    Inputs:
        user_id    - int or None. If None, returns records for all participants.
        start_date - datetime.date or None. Inclusive lower bound on occurred_at.
        end_date   - datetime.date or None. Inclusive upper bound on occurred_at.

    Outputs:
        pd.DataFrame with columns:
            id, user_id, jitai_log_id, event_type, occurred_at, recorded_at.
        One row per engagement event. Timestamps are timezone-aware (UTC).
    """


def load_wearable_devices() -> pd.DataFrame:
    """
    Purpose:
        Load all wearable device records from app_wearabledevice (one per
        participant; OneToOne with user).

    Inputs:
        None.

    Outputs:
        pd.DataFrame with columns:
            id, user_id, labfront_participant_id, is_active, last_synced_at.
        One row per enrolled participant who has registered a device.
    """


def load_ema_item_responses(
    user_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """
    Purpose:
        Load per-item EMA responses from app_emaitemresponse. Required to
        reconstruct the exact MSSD trigger signal used by the decision engine,
        which reads value_numeric where item_id = 'B1' (energy) and item_id =
        'B2' (stress). Filtering on item_id and pivoting on sub_item_id gives
        the per-prompt EMA series that calculate_mssd() consumed.

    Inputs:
        user_id    - int or None. If None, returns records for all participants.
        start_date - datetime.date or None. Inclusive lower bound on the
                     parent EMA's sent_at timestamp.
        end_date   - datetime.date or None. Inclusive upper bound on the
                     parent EMA's sent_at timestamp.

    Outputs:
        pd.DataFrame with columns matching app_emaitemresponse:
            id, ema_id, item_id, sub_item_id, response_type,
            value_numeric, value_choice, value_choices.
        Also includes user_id and sent_at joined from the parent app_ema row
        so callers can group and sort by participant and prompt time without a
        second join.
        One row per (ema_id, sub_item_id). Timestamps are timezone-aware (UTC).

    Usage example (reconstruct B1 energy series for MSSD audit):
        item_df = load_ema_item_responses(user_id=42)
        b1 = item_df[item_df['item_id'] == 'B1'].sort_values('sent_at')
        mssd_series = b1['value_numeric']
    """


#  EMA FEASIBILITY METRICS
# Preregistered feasibility benchmark: 75% completed check-in rate.
# Analytic requirement for AR(1) parameter recovery: 80% (reported separately).


def compute_ema_response_rate(ema_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute completed EMA check-in rate per participant and overall.
        A prompt is 'completed' when all required items are submitted within
        the 60-minute response window (status = 'completed').
        Delivery failures are excluded from the denominator.

    Inputs:
        ema_df - pd.DataFrame from load_ema(). Must contain columns:
                 user_id, status, sent_at, responded_at.

    Outputs:
        pd.DataFrame with columns:
            user_id, delivered, completed, response_rate_pct.
        Last row is the cohort-level aggregate (user_id = 'ALL').
        response_rate_pct = completed / delivered * 100.
        Benchmark: >= 75%.
    """


def flag_in_window_completions(
    ema_df: pd.DataFrame,
    window_minutes: int = 60,
) -> pd.Series:
    """
    Purpose:
        Identify which EMA rows were responded to within the required time
        window. Used to separate in-window completions from late responses.

    Inputs:
        ema_df         - pd.DataFrame from load_ema(). Must contain columns:
                         sent_at, responded_at.
        window_minutes - int. Response window in minutes (default 60).

    Outputs:
        pd.Series of bool, same index as ema_df.
        TRUE  = responded_at - sent_at <= window_minutes AND responded_at is
                not null.
        FALSE = unanswered, expired, or responded outside window.
    """


def compute_response_latency(ema_df: pd.DataFrame) -> pd.Series:
    """
    Purpose:
        Compute response latency (time from prompt delivery to submission) in
        minutes for each EMA row. Returns NaN for unanswered prompts.

    Inputs:
        ema_df - pd.DataFrame from load_ema(). Must contain columns:
                 sent_at, responded_at.

    Outputs:
        pd.Series of float (minutes), same index as ema_df.
        NaN for rows where responded_at is null.
        Latency > 60 min indicates an out-of-window response.
    """


def compute_item_missingness(ema_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute the per-item (mood, stress, energy) missingness rate across all
        delivered EMA prompts, broken down by participant. These are the
        top-level denormalized fields on app_ema.

        For per-B-item (B1–B8) missingness matching the analysis plan, use a
        separate function operating on load_ema_item_responses() instead.

    Inputs:
        ema_df - pd.DataFrame from load_ema(). Must contain columns:
                 user_id, mood, stress, energy.

    Outputs:
        pd.DataFrame with columns:
            user_id, item, missing_count, delivered_count, missing_pct.
        One row per (user_id, item) combination.
        Last block of rows is the cohort-level aggregate (user_id = 'ALL').
    """



# WITHIN-PERSON MSSD
# Core JITAI trigger signal: mean of squared successive differences over
# consecutive answered EMA items.
# Missingness suppression rule: a missing EMA suppresses the difference for
# both the missing row AND the immediately following answered prompt.

def compute_mssd(ema_series: pd.Series) -> float:
    """
    Purpose:
        Compute the Mean of Squared Successive Differences (MSSD) from a
        time-ordered sequence of Likert EMA responses. Applies the missingness
        suppression rule: NaN at position t suppresses both the (t-1, t) and
        (t, t+1) differences.

    Inputs:
        ema_series - pd.Series of float. Time-ordered Likert responses (1-7).
                     NaN encodes a missed / unanswered prompt.

    Outputs:
        float. MSSD over all valid consecutive answered pairs.
        Returns NaN if fewer than 2 valid consecutive pairs exist.
    """


def compute_run_in_mssd(ema_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute within-person MSSD using only Week 1 (run-in baseline) EMA
        responses. Week 1 = enrolled_at through enrolled_at + 6 days.
        No JITAI randomization occurs during this window; data are used solely
        to calibrate individual thresholds for Weeks 2-5.

    Inputs:
        ema_df - pd.DataFrame from load_ema_item_responses() filtered to
                 item_id = 'B1' (energy) or 'B2' (stress), joined with user
                 enrolled_at. Must contain columns: user_id, sent_at,
                 value_numeric, enrolled_at.
                 Do NOT use mood from load_ema() — the decision engine reads
                 B1/B2 from app_emaitemresponse, not EMA.mood.

    Outputs:
        pd.DataFrame with columns: user_id, run_in_mssd.
        One row per participant. NaN if fewer than 2 valid pairs in Week 1.
    """


def compute_within_person_threshold(
    ema_df: pd.DataFrame,
    quantile: float = 0.80,
) -> pd.Series:
    """
    Purpose:
        Compute each participant's within-person MSSD eligibility threshold as
        an expanding quantile over their EMA history. An expanding window
        (shifted forward by one observation) is used so the threshold at time t
        is estimated only from data before t, preventing look-ahead bias.

    Inputs:
        ema_df   - pd.DataFrame from load_ema_item_responses() filtered to
                   item_id = 'B1' (energy) or 'B2' (stress). Must contain
                   columns: user_id, sent_at, value_numeric.
                   Do NOT use mood from load_ema() — the live decision engine
                   reads B1/B2 from app_emaitemresponse to compute observed_mssd
                   stored in JITAILog. Using mood would produce threshold values
                   that disagree with what the engine actually computed.
        quantile - float in (0, 1). Threshold percentile (default 0.80,
                   PI sign-off 2026-07-06).

    Outputs:
        pd.Series of float, indexed by ema_df index.
        Value at row i = quantile of MSSD distribution computed from all
        answered EMA pairs before row i for that participant.

    Note on MSSD formulation: the live engine uses a rolling(window=3) MSSD
    rather than cumulative MSSD over all answered pairs. For an exact audit
    against JITAILog.observed_mssd, match the rolling formulation. For the
    overall feasibility report, cumulative MSSD is acceptable and aligns with
    the construct-validity harness in mssd_validation.py.
    """


def compute_expected_mssd(sigma: float, rho: float) -> float:
    """
    Purpose:
        Compute the theoretical expected MSSD for an AR(1) process with given
        parameters. Used as the ground-truth benchmark for synthetic data
        validation.

    Inputs:
        sigma - float. Stationary standard deviation of the AR(1) process.
        rho   - float. Lag-1 autocorrelation in (-1, 1).

    Outputs:
        float. Expected MSSD = 2 * sigma^2 * (1 - rho).
    """


def compute_ar1_parameters(ema_series: pd.Series) -> Tuple[float, float]:
    """
    Purpose:
        Estimate AR(1) parameters (rho_hat, sigma_hat) from an observed EMA
        time series. Parameter recovery degrades below ~80% response rate;
        this is an analytic requirement distinct from the 75% feasibility
        benchmark and should be reported as a design recommendation for the
        full trial.

    Inputs:
        ema_series - pd.Series of float. Time-ordered Likert responses (1-7).
                     NaN encodes missed prompts; only answered pairs are used.

    Outputs:
        Tuple[float, float]: (rho_hat, sigma_hat).
            rho_hat   - estimated lag-1 autocorrelation.
            sigma_hat - estimated residual standard deviation (more robust
                        than rho_hat at low response rates).
        Both are NaN if fewer than 3 answered observations exist.
    """


# WEARABLE / WEAR TIME
# No explicit wear flag in Garmin exports; non-wear is inferred.
# Waking window: 8:00 AM - 10:00 PM local time (14 h/day).
# Benchmark: >= 8 h wear / day, >= 5 days / week.

def flag_non_wear(hr_df: pd.DataFrame) -> pd.Series:
    """
    Purpose:
        Infer non-wear periods from heart-rate samples. A sample is flagged as
        non-wear if bpm is 0 or null. Garmin exports carry no explicit is_worn
        flag; this is the primary inference method.

    Inputs:
        hr_df - pd.DataFrame from load_heart_rate(). Must contain column: bpm.

    Outputs:
        pd.Series of bool, same index as hr_df.
        TRUE = inferred non-wear (bpm is 0 or NaN).
    """


def identify_wear_gaps(
    hr_df: pd.DataFrame,
    gap_threshold_minutes: int = 120,
) -> pd.DataFrame:
    """
    Purpose:
        Identify contiguous stretches of missing or non-wear HR data during
        waking hours (8:00 AM - 10:00 PM) that exceed the gap threshold.
        A gap > 2 hours is treated as a non-wear episode for wear-time
        calculation.

    Inputs:
        hr_df                 - pd.DataFrame from load_heart_rate(). Must
                                contain columns: user_id, timestamp, bpm.
        gap_threshold_minutes - int. Minimum gap duration to flag (default 120).

    Outputs:
        pd.DataFrame with columns:
            user_id, date, gap_start, gap_end, gap_duration_minutes.
        One row per identified wear gap during waking hours.
    """


def compute_wear_time(hr_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute daily wearable coverage percentage for each participant over the
        standardized waking window (8:00 AM - 10:00 PM, 14 h/day).
        Non-wear = gaps > 2 consecutive hours. Benchmark: >= 8 h/day,
        >= 5 days/week.

    Inputs:
        hr_df - pd.DataFrame from load_heart_rate(). Must contain columns:
                user_id, timestamp, bpm.

    Outputs:
        pd.DataFrame with columns:
            user_id, date, wear_minutes, wear_pct.
        wear_pct = (840 - gap_minutes) / 840 * 100 (840 min = 14 h).
        One row per (user_id, date).
    """


def flag_stale_sync(
    wearable_df: pd.DataFrame,
    threshold_hours: int = 24,
) -> pd.Series:
    """
    Purpose:
        Flag wearable device records whose last sync is older than the threshold.
        Stale syncs indicate the participant may not be wearing the device or
        the Labfront connection has lapsed. Note the inherent 2-5 min sync lag
        from Labfront processing is separate from this staleness check.

    Inputs:
        wearable_df     - pd.DataFrame from load_wearable_devices(). Must
                          contain column: last_synced_at.
        threshold_hours - int. Hours before a sync is considered stale
                          (default 24).

    Outputs:
        pd.Series of bool, same index as wearable_df.
        TRUE = last_synced_at is more than threshold_hours ago or is null.
    """


# JITAI DECISION AUDIT
# Two-stage decision: (1) eligibility - observed_mssd >= within-person
# threshold; (2) randomization - randomization_draw < randomization_probability.
# These stages must not be conflated in any report.
# PI sign-offs (2026-07-06): p = 0.50, daily cap = 4, cooldown = 60 min.


def audit_decision_stages(jitai_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Break down JITAI decision-point records by stage: total evaluated,
        eligible (MSSD threshold met), randomized-to-send, actually sent, and
        not sent. Surfaces the two-stage decision pipeline for reporting.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log(). Must contain columns:
                   user_id, observed_mssd, randomization_draw,
                   randomization_probability, send_prompt, trigger_reason.

    Outputs:
        pd.DataFrame with columns:
            user_id, total_decision_points, eligible, randomized_to_send,
            sent, not_sent, pct_sent_of_eligible.
        One row per participant, plus a cohort-aggregate row (user_id = 'ALL').
    """


def check_cooldown_compliance(
    jitai_df: pd.DataFrame,
    cooldown_minutes: int = 60,
) -> pd.DataFrame:
    """
    Purpose:
        Identify JITAI decision points where the gap between consecutive sent
        prompts for the same participant is shorter than the required cooldown.
        Violations indicate a decision-engine logic error.

    Inputs:
        jitai_df         - pd.DataFrame from load_jitai_log(). Must contain
                           columns: user_id, triggered_at, send_prompt.
        cooldown_minutes - int. Minimum required gap in minutes (default 60).

    Outputs:
        pd.DataFrame with columns:
            user_id, jitai_log_id, triggered_at, previous_triggered_at,
            gap_minutes, violation.
        Only rows where send_prompt = TRUE are included.
        violation = TRUE if gap_minutes < cooldown_minutes.
    """


def check_daily_cap_compliance(
    jitai_df: pd.DataFrame,
    daily_cap: int = 4,
) -> pd.DataFrame:
    """
    Purpose:
        Identify participant-days where the number of sent JITAI prompts
        exceeds the hard daily cap. Active intervention days (Weeks 2-5) only;
        Week 1 run-in is excluded.

    Inputs:
        jitai_df  - pd.DataFrame from load_jitai_log(). Must contain columns:
                    user_id, triggered_at, send_prompt.
        daily_cap - int. Maximum allowed prompts per day (default 4).

    Outputs:
        pd.DataFrame with columns:
            user_id, date, prompts_sent, exceeds_cap.
        One row per (user_id, date). exceeds_cap = TRUE if prompts_sent > daily_cap.
    """


def compute_intervention_dosage(jitai_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute the number of JITAI prompts sent per participant per active
        study day (Weeks 2-5 only; Week 1 run-in excluded from denominator).
        Expected delivery: ~2-3 prompts per eligible day; hard cap = 4.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log() joined with user
                   enrolled_at. Must contain columns: user_id, triggered_at,
                   send_prompt, enrolled_at.

    Outputs:
        pd.DataFrame with columns:
            user_id, date, prompts_sent, is_active_phase.
        One row per (user_id, date). is_active_phase = FALSE for Week 1.
    """

# PUSH DELIVERY FUNNEL
# Conversion chain: push_sent_at -> device_received_at -> receipt_reported_at
# -> engagement event (ema_opened / notification_tapped).

def compute_delivery_funnel(jitai_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute the push delivery funnel: count and percentage of sent prompts
        that reach each stage. Used to diagnose loss at any stage of the
        push-notification pipeline.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log(). Must contain columns:
                   user_id, send_prompt, push_sent_at, device_received_at,
                   receipt_reported_at, delivery_status.

    Outputs:
        pd.DataFrame with columns:
            stage, count, pct_of_sent.
        Stages in order: sent, accepted_by_expo, received_on_device,
        receipt_reported. Includes a cohort-level summary row.
    """


def compute_push_latency(jitai_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute the latency in seconds at each stage transition of the push
        delivery pipeline per JITAI record.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log(). Must contain columns:
                   id, user_id, push_sent_at, device_received_at,
                   receipt_reported_at.

    Outputs:
        pd.DataFrame with columns:
            id, user_id, sent_to_received_sec, received_to_reported_sec,
            total_pipeline_sec.
        NaN for stages where a timestamp is missing.
    """


# ENGAGEMENT ANALYSIS
# Source: app_engagementlog joined to app_jitailog.
# =============================================================================


def compute_engagement_rates(
    engagement_df: pd.DataFrame,
    jitai_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Purpose:
        Compute per-participant rates of opening, acting on, and dismissing
        delivered JITAI prompts. Denominator = prompts where send_prompt = TRUE
        and delivery_status = 'received_on_device'.

    Inputs:
        engagement_df - pd.DataFrame from load_engagement_log(). Must contain
                        columns: user_id, jitai_log_id, event_type.
        jitai_df      - pd.DataFrame from load_jitai_log(). Must contain
                        columns: id, user_id, send_prompt, delivery_status.

    Outputs:
        pd.DataFrame with columns:
            user_id, delivered, opened, acted, dismissed,
            open_rate_pct, act_rate_pct, dismiss_rate_pct.
        One row per participant, plus a cohort-aggregate row (user_id = 'ALL').
    """


def compute_open_latency(
    engagement_df: pd.DataFrame,
    jitai_df: pd.DataFrame,
) -> pd.Series:
    """
    Purpose:
        Compute time (in minutes) from push delivery (push_sent_at) to the
        participant opening the prompt (ema_opened event). Measures
        participant responsiveness to delivered interventions.

    Inputs:
        engagement_df - pd.DataFrame from load_engagement_log(). Must contain
                        columns: jitai_log_id, event_type, occurred_at.
                        Filtered to event_type = 'ema_opened'.
        jitai_df      - pd.DataFrame from load_jitai_log(). Must contain
                        columns: id, push_sent_at.

    Outputs:
        pd.Series of float (minutes), indexed by jitai_log_id.
        NaN where no ema_opened event is recorded.
    """


# PARTICIPANT RETENTION
# Benchmark: enrolled continuously Day 1 through Day 35.
# Missing prompts != dropout; run-in (Week 1) and active phase reported separately.


def compute_retention(user_df: pd.DataFrame) -> Dict:
    """
    Purpose:
        Compute participant retention rate overall and split by study phase
        (run-in Week 1 vs. active Weeks 2-5). Missing EMA prompts are not
        counted as dropout unless there is a formal withdrawal record.

    Inputs:
        user_df - pd.DataFrame of user records. Must contain columns:
                  user_id, is_enrolled, enrolled_at.

    Outputs:
        dict with keys:
            overall_n, retained_n, overall_retention_pct,
            run_in_retained_n, run_in_retention_pct,
            active_retained_n, active_retention_pct.
    """


def classify_dropout(
    user_df: pd.DataFrame,
    ema_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Purpose:
        Distinguish between formal withdrawal (is_enrolled = FALSE) and
        non-responsive participants (is_enrolled = TRUE but no EMA completions
        for >= 7 consecutive days).

    Inputs:
        user_df - pd.DataFrame of user records. Must contain columns:
                  user_id, is_enrolled, enrolled_at.
        ema_df  - pd.DataFrame from load_ema(). Must contain columns:
                  user_id, sent_at, status.

    Outputs:
        pd.DataFrame with columns:
            user_id, classification, last_active_date, days_since_active.
        classification values: 'active', 'formal_withdrawal', 'non_responsive'.
    """


# BIOMETRIC-EMA CONCORDANCE
# Compares physiological volatility (HR-MSSD) with self-report volatility
# (EMA-MSSD) to assess signal concordance for JITAI triggering.


def compute_hr_mssd(
    hr_df: pd.DataFrame,
    window_minutes: int = 60,
) -> pd.DataFrame:
    """
    Purpose:
        Compute rolling MSSD on the heart-rate time series within a sliding
        window. Analogous to EMA-MSSD but derived from the physiological
        signal. Non-wear rows (bpm = 0 or NaN) are excluded from each window.

    Inputs:
        hr_df          - pd.DataFrame from load_heart_rate(). Must contain
                         columns: user_id, timestamp, bpm.
        window_minutes - int. Rolling window size in minutes (default 60).

    Outputs:
        pd.DataFrame with columns:
            user_id, window_end_timestamp, hr_mssd.
        One row per rolling window endpoint per participant.
    """


def compute_hr_ema_concordance(
    hr_df: pd.DataFrame,
    ema_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Purpose:
        Compute per-participant Pearson correlation between HR-MSSD (computed
        in the 60-minute window before each EMA prompt) and EMA-MSSD (computed
        from consecutive answered EMA pairs). Measures the degree to which
        physiological and self-report volatility agree.

    Inputs:
        hr_df  - pd.DataFrame from load_heart_rate(). Must contain columns:
                 user_id, timestamp, bpm.
        ema_df - pd.DataFrame from load_ema_item_responses() filtered to
                 item_id = 'B1' (energy) or 'B2' (stress). Must contain
                 columns: user_id, sent_at, value_numeric, responded_at.
                 Do NOT use mood from load_ema() — use the same B1/B2 signal
                 the decision engine uses so HR concordance reflects the actual
                 trigger signal, not a different EMA construct.

    Outputs:
        pd.DataFrame with columns:
            user_id, pearson_r, p_value, n_paired_observations.
        One row per participant. NaN if fewer than 5 paired observations.
    """


# BIOMARKER SUB-STUDY (TIER 2, HAIR STEROIDS)
# NOTE: hair_sample and hair_hygiene_covariates tables are PLANNED and are
# not yet in production (no Django migrations as of 2026-08-14).
# Cortisol and testosterone concentrations MUST be log-transformed and
# winsorized before any inferential analysis.


def preprocess_hair_steroids(hair_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Apply log transformation and winsorization to raw hair cortisol and
        testosterone concentrations. These preprocessing steps are required
        given the expected right-skew and outliers in pg/mg steroid assay data.

    Inputs:
        hair_df - pd.DataFrame from the planned hair_sample table. Must contain
                  columns: user_id, cortisol_pg_mg, testosterone_pg_mg.

    Outputs:
        pd.DataFrame with original columns plus:
            cortisol_log, testosterone_log   (natural log of raw values),
            cortisol_wins, testosterone_wins (winsorized at 1st-99th pct).
        Rows with values <= 0 are set to NaN before log transformation.
    """


def validate_sample_quality(hair_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Flag hair samples that do not meet minimum quality thresholds for
        reliable steroid assay results. Requirements: >= 1 cm length,
        >= 50 mg mass.

    Inputs:
        hair_df - pd.DataFrame from the planned hair_sample table. Must contain
                  columns: sample_id, user_id, hair_length_cm, sample_mass_mg,
                  assay_method.

    Outputs:
        pd.DataFrame with original columns plus:
            length_ok (bool) - hair_length_cm >= 1.0,
            mass_ok   (bool) - sample_mass_mg >= 50.0,
            usable    (bool) - length_ok AND mass_ok.
    """


def compute_substudy_sample_rate(
    user_df: pd.DataFrame,
    hair_df: pd.DataFrame,
) -> float:
    """
    Purpose:
        Compute the proportion of eligible Tier 2 sub-study participants who
        provided a usable hair sample within the collection window at
        enrollment. Reported as a secondary feasibility metric.

    Inputs:
        user_df - pd.DataFrame of user records. Must contain columns:
                  user_id, is_enrolled.
        hair_df - pd.DataFrame from the planned hair_sample table after
                  validate_sample_quality() has been applied. Must contain
                  columns: user_id, usable.

    Outputs:
        float. usable_sample_count / eligible_participant_count.
        Returns NaN if no eligible participants exist.
    """


# MODERATION FRAMEWORK
# Three pre-specified tiers of baseline moderators (Qualtrics / Baseline).
# Source: data-dictionary.md section 3.1.
# Tier 1 = confirmatory; Tier 2 = confirmatory (biomarker); Tier 3 = exploratory.


def compute_tier1_moderation(
    outcomes_df: pd.DataFrame,
    moderators_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Purpose:
        Estimate heterogeneous treatment effects for Tier 1 confirmatory
        moderators: Urgency (SUPPS-P), Emotion Regulation (DERS-16), Eating
        Motives (TFEQ-R18), Drinking Motives (DMQ-R), and Body Listening
        (MAIA-2). Interaction terms are Treatment x Moderator.

    Inputs:
        outcomes_df   - pd.DataFrame with columns: user_id, outcome_domain,
                        outcome_value, treatment_indicator.
        moderators_df - pd.DataFrame with columns: user_id, supps_p, ders_16,
                        tfeq_r18, dmq_r, maia_2.

    Outputs:
        pd.DataFrame with columns:
            moderator, interaction_estimate, se, p_value, ci_lower, ci_upper.
        One row per Tier 1 moderator.
    """


def compute_tier2_biomarker_moderation(
    outcomes_df: pd.DataFrame,
    biomarker_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Purpose:
        Estimate the Hair Cortisol x Reactive Behavior interaction as a Tier 2
        confirmatory moderation analysis. Cortisol values must be preprocessed
        via preprocess_hair_steroids() before calling this function. ACE score
        is handled as a sensitive moderator under IRB sensitive-data protocols.

    Inputs:
        outcomes_df  - pd.DataFrame with columns: user_id, outcome_domain,
                       outcome_value, treatment_indicator.
        biomarker_df - pd.DataFrame with columns: user_id, cortisol_wins,
                       testosterone_wins, ace_score.

    Outputs:
        pd.DataFrame with columns:
            moderator, interaction_estimate, se, p_value, ci_lower, ci_upper.
        One row per Tier 2 biomarker moderator.
    """


def compute_tier3_exploratory(
    outcomes_df: pd.DataFrame,
    moderators_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Purpose:
        Run exploratory moderation analyses for Tier 3 constructs: Subjective
        Social Status (MacArthur ladder), Everyday Discrimination, and
        Chronotype (rMEQ). Results are hypothesis-generating; appropriate
        corrections for multiple comparisons should be applied downstream.

    Inputs:
        outcomes_df   - pd.DataFrame with columns: user_id, outcome_domain,
                        outcome_value, treatment_indicator.
        moderators_df - pd.DataFrame with columns: user_id, macarthur_ladder,
                        everyday_discrimination, rmeq_score.

    Outputs:
        pd.DataFrame with columns:
            moderator, interaction_estimate, se, p_value, ci_lower, ci_upper,
            bonferroni_corrected_p.
        One row per Tier 3 moderator.
    """


# REPORTING & VISUALIZATION
# Aggregate all preregistered feasibility benchmarks and produce standard
# figures for the analysis report.


def summarize_feasibility(
    ema_df: pd.DataFrame,
    jitai_df: pd.DataFrame,
    hr_df: pd.DataFrame,
    user_df: pd.DataFrame,
) -> Dict:
    """
    Purpose:
        Aggregate all preregistered feasibility benchmarks into a single
        summary dictionary. Top-level entry point for the feasibility analysis.

    Inputs:
        ema_df   - pd.DataFrame from load_ema().
        jitai_df - pd.DataFrame from load_jitai_log().
        hr_df    - pd.DataFrame from load_heart_rate().
        user_df  - pd.DataFrame of user records with enrolled_at.

    Outputs:
        dict with keys:
            ema_response_rate_pct        (benchmark >= 75),
            wear_time_mean_hrs_per_day   (benchmark >= 8),
            wear_time_days_meeting_goal  (benchmark >= 5 days/week),
            retention_pct                (Day 1 through Day 35),
            intervention_dosage_mean_per_day,
            cooldown_violation_count,
            daily_cap_violation_count,
            delivery_funnel_received_pct,
            run_in_mssd_mean.
    """


def plot_ema_completion_over_time(ema_df: pd.DataFrame) -> plt.Figure:
    """
    Purpose:
        Plot daily EMA completion rate as a time series across the full study
        period, with the 75% preregistered benchmark as a horizontal reference
        line. Week 1 run-in is shaded distinctly from Weeks 2-5.

    Inputs:
        ema_df - pd.DataFrame from load_ema(). Must contain columns:
                 user_id, sent_at, status.

    Outputs:
        plt.Figure. Line chart of cohort-level daily completion rate (%)
        over study days 1-35.
    """


def plot_mssd_distribution(jitai_df: pd.DataFrame) -> plt.Figure:
    """
    Purpose:
        Plot the distribution of observed_mssd values at JITAI decision
        points, split by eligibility outcome (eligible vs. not eligible).
        Visualises how the within-person threshold partitions the MSSD space.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log(). Must contain columns:
                   observed_mssd, send_prompt, randomization_draw,
                   randomization_probability.

    Outputs:
        plt.Figure. Overlapping KDE or histogram of observed_mssd for
        eligible vs. ineligible decision points.
    """


def plot_delivery_funnel(jitai_df: pd.DataFrame) -> plt.Figure:
    """
    Purpose:
        Plot the push-notification delivery funnel as a horizontal bar chart
        showing count and percentage of prompts reaching each stage:
        sent -> accepted_by_expo -> received_on_device -> receipt_reported.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log(). Must contain columns:
                   send_prompt, delivery_status, push_sent_at,
                   device_received_at, receipt_reported_at.

    Outputs:
        plt.Figure. Horizontal bar chart of funnel stage conversion rates.
    """


def export_feasibility_report(
    summary_dict: Dict,
    output_path: str,
) -> None:
    """
    Purpose:
        Write the feasibility summary dictionary to a CSV file for offline
        review, PI sharing, or import into SPSS / R.

    Inputs:
        summary_dict - dict returned by summarize_feasibility().
        output_path  - str. File path for the output CSV
                       (e.g., 'analytics/output/feasibility_report.csv').

    Outputs:
        None. Writes a single-row CSV to output_path with one column per
        benchmark metric. Raises FileNotFoundError if the output directory
        does not exist.
    """
