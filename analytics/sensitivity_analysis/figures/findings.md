# Decision Analysis & User generation

Author: Tyler Le

Date: 06/27/2026

## General Analysis
- 89 users with sufficient EMA data analyzed
- Strong MSSD recovery (r = 0.823) validates the volatility measure
    - Low MSSD recoveredd better than high MSSD. Meaning higher sigma and rho contributed more variability in MSSD (HRV)
- Most decisions blocked prompts due to being "below within-person threshold"
- Sigma recovers better than rho, which is a known limitation where rho is clipped from 1 to 5, and with non-perfect response rate. 
- Mean empirical MSSD: 0.953 vs. mean true expected: 1.155

*take a look at /figures/decision_engine/* *


- Most decisions to NOT send are because the user wasn't in a high-volatility state 
- Only ~2.6% meet the threshold, which could be calibrated based later on as we test on initial subjects,
 to decide the conservativeness of our current threshold.
- Safeguards (cooldown, insufficient history) are limiting false positives, but could conclude to poor EMA compliance. 

## Recovery by data observation and response rate (rho, sigma and mssd)

Definitions:
- Green line: 70% recovery -- strong positive correlation between true and recovered values.
- Orange Line: 50% recovery -- meaningful positive correlation.
- Response Rate: the percentage at which a sample response to an EMA.
- Answered Observation: amount of of responses sample responded to.

- MSSD is a robust volatility measure with sparse EMA data
- Sigma remains robust with higher response rate and amount of observations. 
- Rho becomes more volatile but still stable (around r > 0.50) with higher response rate/obs. 

*Robust means how stable the parameter is a given different conditions (response rate, observations, etc,..)*