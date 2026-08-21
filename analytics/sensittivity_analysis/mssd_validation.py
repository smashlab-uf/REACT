"""
MSSD construct-validity harness.

Before we trust MSSD (Mean of Squared Successive Differences) as a measure of
temporal instability, we have to show that it *recovers* the latent volatility
we baked into the AR(1) EMA generator. This script:

  1. Generates sub-cohorts with KNOWN latent parameters (sigma, rho),

  2. Computes the empirical MSSD on the realised Likert series (with missing
     EMAs dropped via NaN propagation through np.diff),

  3. Regresses empirical MSSD against the ground-truth expected MSSD
     ( 2 * sigma^2 * (1 - rho) ). A strong positive linear relationship is the
     construct-validity defence for reviewers: MSSD tracks latent volatility
     even after Likert rounding and missingness.

Empirical MSSD on a series z with missing entries:

    MSSD = (1 / (M-1)) * sum_t (z_{t+1} - z_t)^2

computed only over successive pairs where BOTH prompts were answered (a NaN in
either position drops that difference). Plots are written to
figures/validation/.

SCALE NOTE: The synthetic generator currently produces a 1–5 Likert scale.
Production models enforce 1–7 (MinValueValidator / MaxValueValidator in
models.py). The construct-validity benchmarks (r >= 0.7, sigma/rho recovery)
were established on 1–5; update generate_cohort() to use mu=4.0 and clip to
1–7 before the final feasibility report to confirm benchmarks hold on the
deployed scale. Max squared diff: 1–5 = 16; 1–7 = 36.

STUDY DURATION NOTE: build_validation_cohort() uses days=14 matching the
JITAI-analysis-plan-8.7.pdf feasibility sub-study. scripts.py targets the
full 35-day (5-week) deployment. Confirm study length with Prof. Chang and
update days= accordingly before production use.

Author: @tylerrleee
Date: 06/11/2026
"""

import os

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

from synthetic_generator import generate_cohort
from plotting import plot_config_recovery

FIGURE_DIR = os.path.join(os.path.dirname(__file__), "figures", "validation")


def empirical_mssd(series):
    """
    Mean of squared successive differences for one user's EMA series.

    `series` is a 1-5 Likert array that may contain NaN (missed prompts).
    np.diff across a NaN yields NaN, so dropping NaN differences leaves only
    the successive pairs where both prompts were answered.
    """
    z = np.asarray(series, dtype=float)
    diffs = np.diff(z)
    diffs = diffs[~np.isnan(diffs)]
    
    if diffs.size == 0:
        return np.nan
    return float(np.mean(diffs ** 2))


def recover_ar1_params(series):
    """
    Recover the AR(1) latent knobs (rho, sigma) from one user's *observed* EMA.

    `series` is the realised 1-5 Likert array (with NaN gaps), i.e. the latent
    AR(1) process after mean-shift, Likert rounding/clipping and missingness.
    We demean by the empirical mean of the answered points (not the generator's
    mu, which a real analyst would not know):

      sigma_hat : sample SD of the demeaned answered series. The AR(1)
                  stationary SD equals sigma, so this estimates it directly.
      rho_hat   : lag-1 autocorrelation over *consecutive answered pairs* only
                  (a NaN in either position drops that pair, mirroring the
                  successive-difference logic in `empirical_mssd`).

    Returns (rho_hat, sigma_hat); either is NaN when there is too little data.
    Rounding/clipping bias is expected and is exactly what the recovery table
    surfaces: rounding inflates sigma_hat at small sigma and attenuates rho_hat,
    clipping deflates sigma_hat at large sigma.
    """
    z = np.asarray(series, dtype=float)
    answered = z[~np.isnan(z)]

    if answered.size < 2:
        return np.nan, np.nan

    m = answered.mean()
    sigma_hat = float(answered.std(ddof=1))

    # consecutive answered pairs (both prompts non-NaN)
    a = z[:-1]
    b = z[1:]
    pair = ~np.isnan(a) & ~np.isnan(b)
    a = a[pair] - m
    b = b[pair] - m

    denom = np.sum(a ** 2)
    if a.size < 2 or denom <= 0:
        return np.nan, sigma_hat

    rho_hat = float(np.sum(a * b) / denom)
    return rho_hat, sigma_hat


def _label_for(sigma, rho):
    """Coarse Stable/Volatile tag for the strip comparison.

    Thresholds calibrated for the 1-5 synthetic Likert scale. Re-calibrate
    once generate_cohort() is updated to the 1-7 production scale.
    """
    expected = 2.0 * sigma ** 2 * (1 - rho)

    if expected <= 0.4:
        return "Stable"
    if expected >= 1.6:
        return "Volatile"
    return "Mid"


def build_validation_cohort(days    = 14,
                            ema_per_day = 5,
                            users_per_cell = 12,
                            resp_rate = 0.80,
                            sigmas = (0.3, 0.6, 0.9, 1.2, 1.5),
                            rhos = (0.2, 0.5, 0.8),
                            mu   = 3.0,
                            seed = 7):
    """
    Generate one DataFrame of per-user MSSD records across a (sigma, rho) grid.

    Each grid cell pins the latent parameters to a known value via
    generate_cohort(..., sigma=, rho=, mu=), so every user in the cell shares a
    ground-truth expected MSSD. Returns a tidy frame: one row per user with the
    true and empirically recovered MSSD.
    """
    records = []
    cell = 0
    for sigma in sigmas:
        for rho in rhos:
            cell += 1
            cohort = generate_cohort(
                users   = users_per_cell,
                days    = days,
                ema_per_day = ema_per_day,
                seed = seed + cell,          # distinct stream per cell
                resp_rate = resp_rate,
                mu = mu,
                sigma = sigma,
                rho = rho,
            )
            for uid, g in cohort.groupby("user_id"):
                g = g.sort_values("prompt_idx")
                rho_hat, sigma_hat = recover_ar1_params(g["ema"].to_numpy())
                records.append({
                    "user_id": uid,
                    "true_sigma": sigma,
                    "true_rho": rho,
                    "true_expected_mssd": float(g["true_expected_mssd"].iloc[0]),
                    "empirical_mssd": empirical_mssd(g["ema"].to_numpy()),
                    "rho_hat": rho_hat,
                    "sigma_hat": sigma_hat,
                    "n_answered": int(g["ema"].notna().sum()),
                    "group": _label_for(sigma, rho),
                })

    df = pd.DataFrame.from_records(records)
    # users who answered almost nothing give an undefined / unstable MSSD
    return df.dropna(subset=["empirical_mssd"])


def build_config_table(df):
    """
    One comparison table of true vs. recovered AR(1) parameters per grid cell.

    Collapses the per-user validation frame onto the (true_sigma, true_rho) grid
    (one row per pinned cell), averaging the recovered sigma_hat / rho_hat across
    that cell's users. The recovery_pct columns express recovered / true as a
    percentage (100% = perfect recovery; >100% = over-recovered).

    Returns a tidy DataFrame named, by convention, `data_generation_config`.
    """
    grouped = (df.groupby(["true_sigma", "true_rho"])
                 .agg(recovered_sigma=("sigma_hat", "mean"),
                      recovered_rho=("rho_hat", "mean"),
                      n_users=("user_id", "count"))
                 .reset_index())

    grouped["sigma_recovery_pct"] = 100.0 * grouped["recovered_sigma"] / grouped["true_sigma"]
    grouped["rho_recovery_pct"] = 100.0 * grouped["recovered_rho"] / grouped["true_rho"]

    cols = ["true_sigma", "true_rho", "recovered_sigma", "recovered_rho",
            "sigma_recovery_pct", "rho_recovery_pct", "n_users"]
    return grouped[cols].round(3)


def plot_recovery(df, ax=None):
    """Scatter empirical MSSD vs ground truth with an OLS fit and r / rho."""
    x = df["true_expected_mssd"].to_numpy()
    y = df["empirical_mssd"].to_numpy()

    slope, intercept, r, p, _ = stats.linregress(x, y)
    spearman = stats.spearmanr(x, y).correlation

    if ax is None:
        fig, ax = plt.subplots(figsize=(7, 6))
    else:
        fig = ax.get_figure()

    palette = {"Stable": "tab:green", "Mid": "tab:gray", "Volatile": "tab:red"}
    for grp, sub in df.groupby("group"):
        ax.scatter(sub["true_expected_mssd"], sub["empirical_mssd"],
                   s=22, alpha=0.6, color=palette.get(grp, "tab:blue"),
                   label=f"{grp} (n={len(sub)})")

    xs = np.linspace(x.min(), x.max(), 100)
    ax.plot(xs, slope * xs + intercept, color="black", lw=2,
            label=f"OLS  y = {slope:.2f}x + {intercept:.2f}")

    ax.set_xlabel(r"ground-truth expected MSSD  $2\sigma^2(1-\rho)$")
    ax.set_ylabel("empirical MSSD (observed 1-5 Likert, with missingness)")
    ax.set_title("MSSD recovers latent volatility")
    ax.annotate(f"Pearson r = {r:.3f}\nSpearman $\\rho$ = {spearman:.3f}\n"
                f"p = {p:.1e}",
                xy=(0.04, 0.82), xycoords="axes fraction",
                bbox=dict(boxstyle="round", fc="white", ec="0.7"))
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGURE_DIR, "mssd_recovery.png"), bbox_inches="tight")
    return slope, r, spearman


def plot_group_separation(df, ax=None):
    """Strip + box comparison of empirical MSSD across Stable/Mid/Volatile."""
    order = ["Stable", "Mid", "Volatile"]
    groups = [df.loc[df["group"] == g, "empirical_mssd"].to_numpy() for g in order]

    if ax is None:
        fig, ax = plt.subplots(figsize=(7, 5))
    else:
        fig = ax.get_figure()

    ax.boxplot(groups, showfliers=False, widths=0.5)
    ax.set_xticks(range(1, len(order) + 1))
    ax.set_xticklabels(order)
    palette = {"Stable": "tab:green", "Mid": "tab:gray", "Volatile": "tab:red"}
    rng = np.random.default_rng(0)
    for i, (g, vals) in enumerate(zip(order, groups), start=1):
        jitter = rng.normal(0, 0.05, len(vals))
        ax.scatter(np.full(len(vals), i) + jitter, vals,
                   s=14, alpha=0.5, color=palette[g])

    ax.set_ylabel("empirical MSSD")
    ax.set_title("Empirical MSSD separates Stable vs. Volatile cohorts")
    fig.tight_layout()
    fig.savefig(os.path.join(FIGURE_DIR, "mssd_group_separation.png"),
                bbox_inches="tight")


def main():
    os.makedirs(FIGURE_DIR, exist_ok=True)

    df = build_validation_cohort()
    slope, r, spearman = plot_recovery(df)
    plot_group_separation(df)

    # True vs. recovered (sigma, rho) per grid cell -- one comparison table.
    data_generation_config = build_config_table(df)
    config_path = os.path.join(FIGURE_DIR, "data_generation_config.csv")
    data_generation_config.to_csv(config_path, index=False)
    plot_config_recovery(data_generation_config)  # true vs. recovered scatter

    # Same comparison, one row per user (true + recovered side by side). Rename
    # the recovered columns to match the per-cell table; leave `df` untouched so
    # the plotting/aggregation above still references sigma_hat / rho_hat.
    per_user_cols = ["user_id", "group", "n_answered",
                     "true_sigma", "recovered_sigma", "true_rho", "recovered_rho",
                     "true_expected_mssd", "empirical_mssd"]
    per_user = (df.rename(columns={"sigma_hat": "recovered_sigma",
                                   "rho_hat": "recovered_rho"})[per_user_cols]
                  .round(3))
    per_user_path = os.path.join(FIGURE_DIR, "param_recovery_per_user.csv")
    per_user.to_csv(per_user_path, index=False)

    print("data_generation_config  : true vs. recovered AR(1) params per cell")
    print(data_generation_config.to_string(index=False))
    print()
    print(f"users validated         : {len(df)}")
    print(f"OLS slope               : {slope:.3f}")
    print(f"Pearson r               : {r:.3f}")
    print(f"Spearman rho            : {spearman:.3f}")
    print(f"config table written to : {config_path}")
    print(f"per-user table written  : {per_user_path}")
    print(f"figures written to      : {FIGURE_DIR}")

    if r >= 0.7:
        print("PASS: strong positive correlation -> MSSD construct validated.")
    else:
        print("WARN: weak correlation -> revisit generator / parameters.")


if __name__ == "__main__":
    main()