# Decision Analysis & User generation

Author: Tyler Le

Date: 06/27/2026 (re-run on the 1–7 Likert scale 10/09/2026)

All numbers below are on the production 1–7 scale, from `sensitivity_analysis.ipynb`.

## General Analysis
- 100 users with sufficient EMA data (>= 10 answered) analyzed
- Strong MSSD recovery (r = 0.849, Spearman 0.871, OLS slope 0.73) validates the volatility measure
    - Low MSSD recoveredd better than high MSSD. Meaning higher sigma and rho contributed more variability in MSSD (HRV)
- Most decisions blocked prompts due to being "below within-person threshold"
- Sigma recovers better than rho (r = 0.888 vs 0.552), a known limitation of Likert rounding and non-perfect response rate. 
- Mean empirical MSSD: 1.086 vs. mean true expected: 1.107 (on 1–5 this was 0.953 vs 1.155; the wider scale clips less)

*take a look at /figures/decision_engine/* *


- Most decisions to NOT send are because the user wasn't in a high-volatility state 
- 12.8% of decision points send a prompt (51.3% below threshold, 27.8% missing EMA, 7.9% insufficient
 history). The earlier "~2.6%" could not be reproduced from any committed decision log. The threshold can be
 recalibrated once initial subjects are tested, to decide how conservative it should be.
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