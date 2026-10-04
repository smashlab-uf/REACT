# WCLS for the REACT micro-randomized trial: a beginner's guide

The implementation is `analytics/wcls_analysis.ipynb`. The study-level plan is in
`JITAI-analysis-plan.md` § Micro-randomized trial.

WCLS (weighted and centered least squares) is ordinary regression with two adjustments added and one
common modelling shortcut avoided. Each exists because an MRT differs from a normal experiment in a
specific way. This guide builds up from the causal question, then shows where WLS, GEE and mixed models
go wrong.

---

## 1. The question: what would have happened otherwise?

Say participant 12 finishes a check-in at 3:10 pm, the engine finds them eligible, and the coin says
**send**. Their next check-in, 90 minutes later, shows valence = 5.

Was the prompt helpful? You'd need to know what that check-in would have said **without** the prompt.
Causal inference calls these the two **potential outcomes**:

- $Y(1)$: valence at the next check-in if we send
- $Y(0)$: valence at the next check-in if we don't

The causal effect of this prompt at this moment is $Y(1) - Y(0)$. You only ever see one of the two.
This is the **fundamental problem of causal inference**, and every method in this field is a way around it.

## 2. What randomization buys you

You can't see both outcomes for one moment. But you can compare many sent moments with many unsent
moments, as long as the two groups are alike in everything except the prompt.

Randomization guarantees that. Because a coin decides, "sent" moments aren't systematically moodier,
later in the day or more engaged than "unsent" ones. In the **average** over many moments, the
difference in outcomes is the causal effect.

This is why an experiment is simpler than an observational study. In observational data you add control
variables to remove **confounding**, meaning differences between the groups other than the treatment.
**In a randomized study, control variables aren't needed for correctness. They only reduce noise.** Keep
this in mind, because it's the key to why WCLS is built the way it is.

## 3. Why an MRT isn't an ordinary experiment

A standard randomized trial flips one coin per person. REACT flips a coin at **every available decision
point**, hundreds per participant across the season. That brings four complications:

| Complication | What it looks like in REACT |
|---|---|
| **Repeated measures** | Each person contributes many rows, and rows from the same person are correlated: a generally happy person is happy at many moments. |
| **Treatment changes the future** | A prompt at 3 pm changes the next check-in. That check-in is itself the next decision point: its valence becomes `prior_valence` for the next row and feeds the MSSD that decides the next moment's eligibility. |
| **Availability** | Some moments were never randomized (run-in, below threshold, cooldown, cap). The effect only exists for available moments. |
| **Effects can vary** | A prompt may help more in the evening, early in the season, or for more volatile people. |

## 4. What exactly are we estimating?

Because treatments feed forward, "the effect of a prompt" has to be defined carefully. The MRT answer is
the **causal excursion effect**.

Picture the study running normally, coin flips and all, up to 3 pm today. At 3 pm we step outside the
protocol and force "send" in one copy of the world and "don't send" in another, then compare the next
check-in:

$$
\beta = \mathbb{E}\big[\,Y_{t+1}(\text{send now}) - Y_{t+1}(\text{don't send now}) \;\big|\; \text{available}\,\big]
$$

The average is over all the histories the real study produces, including all the earlier prompts people
got or didn't get.

It's called an **excursion** because it's a one-moment departure from the protocol. It is **not** the
effect of always sending versus never sending; that is a season-level question.

With a moderator $S$ such as hour of day, $\beta(S)$ is the same effect, averaged separately at each value
of $S$. For example: at 3 pm, on average, how much does a prompt help?

Why average instead of estimating the effect for each exact situation? With 35 people you can't estimate
an effect for every combination of mood, time, history and day. What you can estimate is the effect
averaged over everything except a few chosen moderators. That is also the quantity a JITAI designer needs:
"sending at time X helps by this much on average."

## 5. WCLS, one ingredient at a time

The regression the notebook fits is:

$$
Y_{t+1} = \underbrace{\alpha_0 + \alpha_1\,\text{prior valence} + \alpha_2\,\text{prior stress} + \dots}_{\text{controls: absorb noise}} \;+\; (A_t - \tilde p)\,\big(\beta_0 + \beta_1 S_t\big)
$$

Each row has weight $W_t = (\tilde p/p_t)^{A_t}\,\big((1-\tilde p)/(1-p_t)\big)^{1-A_t}$, and only available
rows are used.

### Ingredient 1: centering (the "C")

The treatment enters as $A - \tilde p$, not as $A$. With $\tilde p = 0.5$, a sent moment gets $+0.5$ and
an unsent one gets $-0.5$. That looks cosmetic, but it's what makes the method robust.

For any given history, the centered and weighted treatment averages exactly zero:

$$
\mathbb{E}[\,W(A-\tilde p)\mid H\,] = \underbrace{p\cdot\tfrac{\tilde p}{p}(1-\tilde p)}_{\text{sent}} + \underbrace{(1-p)\cdot\tfrac{1-\tilde p}{1-p}(0-\tilde p)}_{\text{not sent}} = \tilde p(1-\tilde p) - \tilde p(1-\tilde p) = 0
$$

A column with zero mean given every possible history is **uncorrelated with any function of the
history**. So whatever the control part of the model gets wrong cannot leak into $\beta$. You might forget
that evenings are happier, model prior valence as linear when it isn't, or leave out stress entirely. Each
of those costs precision (wider intervals) but doesn't bias $\beta$.

Put simply, the model has two parts:

- The **control part** is a noise-absorber, and it's allowed to be wrong.
- The **treatment part** is protected by randomization and centering.

This matches the point from §2: in an experiment, controls are for precision, not correctness.

### Ingredient 2: weighting (the "W")

$p_t$ is the probability the coin actually used at that moment. $\tilde p$ is a probability you choose,
here the average of the logged $p$. The weight reshapes the data so it looks as if every moment had been
randomized with probability $\tilde p$.

This matters when $p$ varies, say 0.3 in the morning and 0.6 in the afternoon. Afternoon moments would
then be over-represented among sends. If afternoons are also happier, sends would look good even with no
real effect: confounding introduced by the design itself. The weights undo that.

**In REACT today, $p$ is always 0.5, so every weight is exactly 1.** It still matters for two reasons:

- `JITAI_RANDOMIZATION_PROBABILITY` is an environment variable. If the PI changes it mid-season, or
  Phase 1 and Phase 2 run at different values, $p$ varies over time.
- `randomization_probability` is logged on every row, so WCLS handles such a change automatically. A
  regression that ignores $p$ would not.

The Monte Carlo in the notebook deliberately uses $p$ = 0.3 before noon and 0.6 after, to test this.

### Ingredient 3: treat rows as independent, then correct the uncertainty

The point estimate treats every row as if it were independent. That sounds naive, but it's deliberate,
and §6 explains why the alternative is dangerous.

The within-person correlation is handled afterwards, in the **standard error**. The **cluster-robust
(sandwich) variance** treats each participant as one independent unit, however many rows they contribute.
The notebook adds a small-sample correction (Mancl–DeRouen) and uses a t distribution, because sandwich
SEs are too small when there are only 5–35 people.

## 6. Where the other methods fall short

### A. Plain regression (OLS) with uncentered treatment

Suppose you fit `Y ~ prior_valence + A + A:hour`, which is the obvious way to ask whether the effect
depends on hour, and you forget the main effect of `hour`.

- **Truth:** evenings are just happier, and the prompt's effect is the same all day.
- **What goes wrong:** the column `A × hour` is correlated with `hour`, because it is `hour` on sent
  moments and 0 otherwise. Its coefficient picks up "evenings are happier" and reports it as **"the prompt
  works better in the evening."** That is a moderation finding produced entirely by the model.
- **The WCLS version:** $(A - 0.5)\times\text{hour}$ averages zero at every hour, so it has no correlation
  with `hour`. The spurious moderation disappears.

This is the robustness from Ingredient 1, shown on a concrete case. Plain OLS is only as good as your
control model. WCLS doesn't depend on the control model being right.

OLS also has two further problems:

- Its default standard errors treat 500 rows from 30 people as 500 independent observations, so they are
  far too small.
- If $p$ varies, it ignores that, as described under Ingredient 2.

### B. Weighted least squares (WLS)

The name is similar, but the weights do a different job:

| | WLS | WCLS |
|---|---|---|
| Purpose of the weights | **Efficiency**: noisy rows count less (inverse variance) | **Re-randomization**: make the data look as if randomized at $\tilde p$ |
| What the weights depend on | The outcome's variance, often through a model | Only the randomization probabilities, which are known exactly |
| What $\beta$ you get | An effect tilted toward low-noise moments | The average effect over the moments the study actually produces |

If you weight by inverse variance, you are quietly asking a different question: "what is the effect at the
calm, predictable moments?" If the prompt works best at chaotic, high-variance moments, which are what a
JITAI targets, WLS down-weights exactly those. WCLS's weights come from the known coin, so they can't pull
the estimate toward any kind of moment. Standard WLS also has the same clustering and centering problems as
OLS.

### C. GEE with a correlation structure (exchangeable, AR(1))

This is the subtlest problem and the most important one.

GEE looks tailor-made for repeated measures. You tell it rows from the same person are correlated, for
example with an AR(1) structure where nearby moments are more alike, and it uses that to estimate more
efficiently. To do so, it uses information from **other** time points of the same person, including
**later** ones, when estimating the relationship at time $t$.

That is only valid under an assumption that is rarely stated (Pepe & Anderson, 1994):

> The outcome at time $t$ must be unrelated to the covariates at *other* times, once you know the
> covariates at time $t$.

In REACT, this assumption fails **by construction**:

```
decision t:  A_t ──► next check-in valence  = Y_{t+1}   (the outcome for decision t)
                                       │
                                       └──► prior_valence at decision t+1  (a covariate)
                                       └──► MSSD at t+1 ──► availability at t+1
```

The outcome of decision $t$ is literally the covariate of decision $t+1$, and it also helps decide whether
$t+1$ is available at all. With a non-independence working correlation, GEE mixes this future information
back into the estimate at time $t$ and **biases $\beta$**. More data doesn't fix it.

WCLS therefore takes GEE's safe option, **working independence** (Ingredient 3):

- Estimate as though rows were independent, which only uses "outcome given covariates at the same
  moment", and that is always valid.
- Then correct the standard errors with the sandwich.

You give up a little efficiency and avoid the bias. GEE with an independence structure is in fact close to
WCLS; what's missing is centering and weighting, which leaves it with problem A.

### D. Mixed models (random intercepts)

Mixed models are the usual default in psychology, so they're worth a mention. A model like
`valence ~ A + prior_valence + (1 | participant)` has two problems here:

- **Biased with lagged outcomes.** A person's random intercept ("generally happy") also drives their
  `prior_valence`. A covariate correlated with the random effect violates the model's assumptions and
  biases the coefficients (Qian, Klasnja & Murphy, 2020, studied this for MRTs specifically).
- **Different estimand.** A mixed model estimates the effect **for a person with a given random effect**.
  The MRT question is the **average** effect across the moments the study produces.

### Summary

| Method | Robust to a wrong control model? | Handles varying $p$? | Safe when outcomes feed into later covariates? | Standard errors |
|---|---|---|---|---|
| OLS (uncentered) | ✗ | ✗ | ✓ (each row stands alone) | ✗ unless clustered |
| WLS (inverse variance) | ✗ | ✗ | ✓ | ✗, and the estimand shifts |
| GEE, AR(1) / exchangeable | ✗ | ✗ | **✗: biased** | ✓ |
| Mixed model | ✗ | ✗ | **✗: biased with lagged outcomes** | model-based |
| **WCLS** | **✓ (centering)** | **✓ (weights)** | **✓ (working independence)** | **✓ (clustered + small-sample)** |

## 7. What this means for REACT in practice

In the current study (p = 0.5, controls including the moderators, working independence), WCLS gives
**numerically the same β** as an OLS on the centered design. The notebook checks this against statsmodels
in §7.

So there's no special estimation algorithm. The value of WCLS is that it tells you which choices matter:

1. Center the treatment at $\tilde p$, or your moderator findings can be artifacts.
2. Use the coin (`draw < p`), not `send_prompt`, as the treatment, and only available rows.
3. Weight by the logged $p$, so a mid-season change to the probability doesn't bias the result.
4. Treat rows as independent for estimation, because in REACT outcomes become future covariates, which
   breaks AR(1) GEE and mixed models.
5. Cluster standard errors by person, with a small-sample correction, because 5–35 people is very few.

Getting any one of these wrong usually won't produce an error. It produces a confident, plausible and wrong
number.

## 8. What WCLS does not fix

- **Missing outcomes.** If a moment has no check-in in the next 2 h, it's dropped. If missingness depends
  on the arm (reminders are suppressed after a send) and on how the person feels, that can bias β. This is
  the biggest threat in REACT, which is why the notebook reports missingness by arm first.
- **Proximal only.** β describes the next 2 hours, not the season.
- **Assignment, not receipt.** A failed push still counts as "sent." The effect of actually receiving a
  prompt is the IV analysis in the analysis plan.
- **Few people.** With n = 5, even the corrected SEs have almost no degrees of freedom, so Phase 1 supports
  only the simplest model.

## 9. References

- Boruvka, A., Almirall, D., Witkiewitz, K., & Murphy, S. A. (2018). Assessing time-varying causal effect
  moderation in mobile health. *Journal of the American Statistical Association*, 113(523), 1112–1121.
  The WCLS paper.
- Qian, T., Walton, A. E., Collins, L. M., Klasnja, P., Lanza, S. T., Nahum-Shani, I., et al. (2022). The
  microrandomized trial for developing digital interventions: Experimental design and data analysis
  considerations. *Psychological Methods*, 27(5), 874–894. A more approachable version aimed at
  behavioural scientists.
- Qian, T., Klasnja, P., & Murphy, S. A. (2020). Linear mixed models with endogenous covariates: Modeling
  sequential treatment effects with application to a mobile health study. *Statistical Science*, 35(3),
  375–390.
- Pepe, M. S., & Anderson, G. L. (1994). A cautionary note on inference for marginal regression models with
  longitudinal data and general correlated response data. *Communications in Statistics — Simulation and
  Computation*, 23(4), 939–951.
- Mancl, L. A., & DeRouen, T. A. (2001). A covariance estimator for GEE with improved small-sample
  properties. *Biometrics*, 57(1), 126–134.
