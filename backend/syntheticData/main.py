from synthetic_generator import generate_user_ids, generate_cohort, generate_HR
import pandas as pd
from plotting import (
    plot_gap_histogram,
    plot_missing_rate_histogram,
    plot_response_raster,
    plot_heart_rate,
)

if __name__ == "__main__":

    # TWEAK PARAMETERS HERE
    USERS = 100
    DAYS = 7
    EMA_PER_DAY = 5
    RESP_RATE = 0.80
    SEED = 42

    # Shared UUIDs so EMA and HR data can be joined
    user_ids = generate_user_ids(USERS)

    # Generate EMA data
    ema_df = generate_cohort(
        users=USERS,
        days=DAYS,
        ema_per_day=EMA_PER_DAY,
        seed=SEED,
        resp_rate=RESP_RATE,
        user_ids=user_ids,
    )

    # Generate HR data
    hr_df = generate_HR(
        users=USERS,
        days=DAYS,
        seed=SEED,
        user_ids=user_ids,
    )

    # Plot diagnostics (all save to ./syntheticData/figures/)
    plot_gap_histogram(ema_df, mean_gap_length=3)
    plot_missing_rate_histogram(ema_df, resp_rate=RESP_RATE)
    plot_response_raster(ema_df, resp_rate=RESP_RATE)

    target_date = pd.to_datetime('2026-06-02').date()
    sample_hr = hr_df[
        (hr_df["user_id"] == user_ids[0]) & 
        (hr_df['timestamp'].dt.date == target_date)
    ]
    plot_heart_rate(sample_hr)  # single user for readability | add more if needed

    print('DONE RUNNING')