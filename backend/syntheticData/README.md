# syntheticData

*Tyler Le*

*Date: 06/02/2026*

[GITHUB](<https://github.com/tylerrleee/HealthyGatorSportFan/tree/mssd-tyler>)

Synthetic data generation for EMA (Ecological Momentary Assessment) and heart rate (HR) signals, used to validate MSSD (Mean of Squared Successive Differences) as a measure of temporal instability.

# Scope

We are focusing on qualitative (EMA) and quantitative simulation (HR), so other quantitative values (steps, sleep, etc,..) are omitted for the purpose of simulation concept review and simplicity of debugging.

## Files

### `main.py`

Generates a cohort of 100 synthetic users over 7 days, producing both EMA and heart rate DataFrames (DF). Runs all diagnostic plots and saves them to `figures/`.

In scope: Parameters can be tweaked to simulate different cohorts, producing a distinct DF. 

Out of scope: DF does not save. 

### `synthetic_generator.py`

Core data generation module. Contains two generators:

- **EMA Generator** — Produces per-user EMA time series with known latent volatility using an AR(1) process (see below about why AR(1)). 
    - Each user gets randomly drawn parameters (`mu`, `sigma`, `rho`) that serve as ground truth for validating MSSD. EMA values are mapped to a 1-5 Likert scale (for now). Missingness or no response is injected via randomness (`_clustered_missing_mask`) that produces realistic clustered gaps rather than random dropout.
    - No response are clustered, instead of purely random. Although this missingness can be controlled, it helps simualate the prolonged responsiveness of certain individuals
        - e.g. phone is on DND, has an exam, watching the Knicks winning NBA Finals, etc,..
    
  - `generate_user_ids()` - Creates unique UUIDs for each user.
  - `generate_user()` - Generates one user's EMA series with known latent parameters.
  - `generate_cohort()` - Generates the full cohort DataFrame.

- **Heart Rate Generator** — Produces minute-level HR data per user, random Gaussian noise, and activity bouts (exercise spikes). Resting HR is estimated from [60,100] for young adults. 
  - `_generate_heart_rate()` — Generates one user's minute-level HR series.
  - `generate_HR()` — Generates HR data for the full cohort.
    - Parameters are tweakable in `synthetic_generator.py`

#### First-Order Autoregressive Model

*['Autoregressions', Economics-With-R](<https://www.econometrics-with-r.org/14.3-autoregressions.html>)*

An autoregressive model relates a time series variable to its past values.

In our case, the goal is to 'model' a person's fluctuating psychological state, which is **dependent on the previous state**.

Formula:

$$
z_t = \rho * z_{t-1} + e_t
$$

$z_t$: User's state

$z_{t-1}$: User's state at the previous time step

$e_t$ : Random noise that influence user's state (e.g. FSU just scored, just took some cognac, hitting a PR)

e_t ~ $Normal(0, \sigma_{e^2})$ : random noise is Normally distributed.
  - On average, most noise are close to the mean, 0, but sometimes, depending $\sigma_{e^2}$, it can be influential

### `plotting.py`

Diagnostic visualizations for validating the synthetic data. All figures are saved to `figures/`.

- `plot_gap_histogram()` — Distribution of missing-run lengths vs. the expected geometric distribution.
- `plot_missing_rate_histogram()` — Per-user missing rate distribution compared to the target response rate.
- `plot_response_raster()` — Side-by-side raster comparing clustered (sticky) missingness against an MCAR baseline.
- `plot_heart_rate()` — Heart rate over time for a single user.

### `notebook/Tien_Le_MSSD_v1_0.ipynb`

Research notebook documenting the MSSD methodology

### `figures/`

Output directory for saved plots:

- `gap_histogram.png` - Missing-run length distribution.

![1](./figures/gap_histogram.png)

- `missing_rate_histogram.png` - Per-user missing rate histogram.

![2](./figures/missing_rate_histogram.png)

- `heart_rate_analysis.png` - Sample heart rate time series.

![3](./figures/heart_rate_analysis.png)

- `response_raster.png` - Sticky vs. scatter missingness raster.

![4](./figures/response_raster.png)



Here is a structured, comprehensive implementation plan to articulate your next steps. This plan bridges your current synthetic data generation framework with the updated database schema, keeping the focus tight on construct validation and the migration to Garmin specs.

---

## Technical Implementation Plan: MSSD Validation & Garmin Data Integration

### 1. Construct Validity Defense: MSSD Volatility Recovery

Before storing data, we must prove that our First-Order Autoregressive $AR(1)$ process truly serves as a reliable ground truth for Mean of Squared Successive Differences (MSSD) validation.

* **The Goal:** Verify that a higher preset latent volatility ($\sigma_{e^2}$ or lower $\rho$) in the EMA generator mathematically maps to a higher calculated MSSD from the generated 1–5 Likert scale output.
* **The Test Harness:** * Generate a test cohort where sub-groups are assigned distinct, known parameters (e.g., Stable vs. Volatile cohorts).
* Calculate empirical MSSD on the generated time series using:

$$MSSD = \frac{1}{N-1} \sum_{t=1}^{N-1} (z_{t+1} - z_t)^2$$


* **Success Metric:** Plot Ground Truth Variance vs. Empirical MSSD. A strong, positive linear correlation validates our construct defense for reviewers, proving the simulation works as an effective benchmark despite missingness masks.



---

## 2. Wearable Migration & HRV Field Extension (Garmin Spec)

We are shifting our hardware profile simulation from Fitbit to Garmin (Venu 3 / Vivoactive 5/6 specs). Garmin devices handle heart rate and Heart Rate Variability (HRV) metrics differently, which requires updates to both our generation math and schema.

### Data Engineering Updates

* **Schema Adaptations:**
* `heart_rate_sample`: Ensure the `source` field explicitly flags `'Garmin Venu 3'` or `'Garmin Vivoactive'`.
* `stress_sample`: Garmin calculates stress via continuous HRV (inter-beat intervals or RMSSD) mapped to a 0–100 score. Our simulator must generate an algorithmic correlation between `heart_rate_sample` spikes (exercise bouts) and `stress_sample` responses.



### HRV Generation Mechanics

We will inject a new HRV field into the simulation. In a real-world setting, a high heart rate corresponds to a compressed, less variable inter-beat interval (lower HRV), while resting states elevate HRV.

* **The Simulation Formula:** Tie the minute-level baseline heart rate ($HR_t$) inversely to the generated HRV ($RMSSD_t$):

$$RMSSD_t = \alpha \cdot \left( \frac{100}{HR_t} \right) + \epsilon_t$$



*(Where $\alpha$ is a scaling factor matching young adult Garmin ranges, and $\epsilon_t$ captures normal somatic noise).*

---

## 3. Database Wiring & ETL Pipeline Architecture

Once validated, the data frames generated by `synthetic_generator.py` need to be persistent. Based on your relational schema, we will construct a clean database push routine.

```
[synthetic_generator.py]
         │
         ▼
[MSSD Validation Test] ──(Pass)──► [Database Push Module (SQLAlchemy)]
                                                 │
                                                 ├──► user
                                                 ├──► wearable_device
                                                 ├──► heart_rate_sample
                                                 └──► ema

```

### Relational Execution Order

To respect foreign key constraints, the pipeline must push data in this exact sequential order:

1. **`user` Table:** Populate UUID-mapped `user_id`, mock names, and demographic details.
2. **`wearable_device` Table:** Link an active device `id` to the `user_id` with `device_name = 'Garmin Venu 3'`.
3. **`heart_rate_sample` & `stress_sample` Tables:** Bulk insert the minute-level quantitative arrays linked via `user_id`.
4. **`ema` Table:** Insert the 1–5 Likert scale responses, writing missing entries explicitly as `status = 'missed'` or omitting them based on the `_clustered_missing_mask`.

---

## Action Item Roadmap

| Phase | Task | Deliverable |
| --- | --- | --- |
| **Phase 1** | Implement **MSSD validation .py file** to correlate known input parameters with empirical outcomes. PLot to syntheticData/figures/validation/ | Regression plot verifying recovery. |
| **Phase 2** | Refactor `synthetic_generator.py` to add `_generate_hrv_and_stress()` using **Garmin specs**. | Extended output DataFrames with HRV metrics. |
| **Phase 3** | Build out automated database seed script utilizing **SQLAlchemy / SQL** bulk inserts. | Clean, uncorrupted database instance populated with 100 mock users. |
