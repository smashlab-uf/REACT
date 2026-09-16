import logging
from pathlib import Path
import unittest

import pandas as pd
from pandas.testing import assert_series_equal

from decision_engine.decision_engine import (
    apply_decision_rules,
    attach_rmssd_to_decisions,
    calculate_mssd,
    compute_rmssd_5min_series,
    compute_rmssd_from_bbi,
)


logger = logging.getLogger(__name__)


class DecisionEngineScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario_path = Path(__file__).with_name("scenario_test_outputs.csv")
        cls.expected = pd.read_csv(
            cls.scenario_path,
            parse_dates=["timestamp", "scheduled_timestamp"],
        ).sort_values(["user_id", "timestamp"]).reset_index(drop=True)

        source = cls.expected[["user_id", "timestamp", "ema"]].copy()
        cls.actual = (
            apply_decision_rules(calculate_mssd(source))
            .sort_values(["user_id", "timestamp"])
            .reset_index(drop=True)
        )

        logger.info(
            "Loaded %s scenario rows across %s scenarios",
            len(cls.expected),
            cls.expected["scenario"].nunique(),
        )

    def test_scenario_file_has_expected_cases(self):
        scenarios = set(self.expected["scenario"].dropna().unique())

        self.assertEqual(
            scenarios,
            {"low_volatility", "high_volatility", "missing_and_late"},
        )
        self.assertFalse(self.expected.empty)

    def test_engine_reproduces_scenario_outputs(self):
        comparable_columns = [
            "ema_diff_squared",
            "observed_mssd",
            "user_threshold",
            "send_prompt",
            "decision_reason",
        ]

        for column in comparable_columns:
            with self.subTest(column=column):
                assert_series_equal(
                    self.actual[column],
                    self.expected[column],
                    check_names=False,
                    check_dtype=False,
                    atol=1e-9,
                    rtol=1e-9,
                )

    def test_missing_and_late_scenario_tracks_late_responses(self):
        missing_and_late = self.expected[self.expected["scenario"] == "missing_and_late"]

        self.assertGreater(missing_and_late["ema"].isna().sum(), 0)
        self.assertGreater(missing_and_late["minutes_late"].max(), 0)


class PromptFiringTests(unittest.TestCase):

    def _volatile_df(self):
        base = pd.Timestamp('2026-01-01 09:00:00')
        stable = [
            {'user_id': 1, 'timestamp': base + pd.Timedelta(hours=i), 'ema': 4.0}
            for i in range(5)
        ]
        spike = {'user_id': 1, 'timestamp': base + pd.Timedelta(hours=5), 'ema': 1.0}
        return pd.DataFrame(stable + [spike])

    def test_high_volatility_fires_at_least_one_prompt(self):
        result = apply_decision_rules(calculate_mssd(self._volatile_df()))
        self.assertTrue(result['send_prompt'].any())
        self.assertIn('prompt sent', result['decision_reason'].values)

    def test_stable_signal_fires_no_prompts(self):
        base = pd.Timestamp('2026-01-01 09:00:00')
        df = pd.DataFrame([
            {'user_id': 1, 'timestamp': base + pd.Timedelta(hours=i), 'ema': 4.0}
            for i in range(8)
        ])
        result = apply_decision_rules(calculate_mssd(df))
        self.assertFalse(result['send_prompt'].any())

    def test_send_prompt_true_only_when_decision_reason_is_prompt_sent(self):
        result = apply_decision_rules(calculate_mssd(self._volatile_df()))
        expected = result['decision_reason'] == 'prompt sent'
        self.assertTrue((result['send_prompt'] == expected).all())


class HrvLoggingTests(unittest.TestCase):

    def test_rmssd_matches_known_intervals(self):
        values = pd.Series([800, 810, 790])
        self.assertAlmostEqual(compute_rmssd_from_bbi(values), 15.8113883)

    def test_decision_hrv_annotation_does_not_change_decision_columns(self):
        timestamp = pd.Timestamp('2026-06-01 09:05:00')
        decisions = pd.DataFrame([{
            'user_id': 'user-1',
            'timestamp': timestamp,
            'ema': 4,
            'send_prompt': True,
            'decision_reason': 'prompt sent',
        }])
        bbi = pd.DataFrame({
            'user_id': ['user-1'] * 5,
            'timestamp': pd.date_range('2026-06-01 09:00:00', periods=5, freq='s'),
            'bbi_ms': [800, 810, 790, 805, 795],
        })

        result = attach_rmssd_to_decisions(decisions, bbi, min_beats=5)

        self.assertTrue(result.loc[0, 'send_prompt'])
        self.assertEqual(result.loc[0, 'decision_reason'], 'prompt sent')
        self.assertEqual(result.loc[0, 'rmssd_count'], 5)
        self.assertFalse(pd.isna(result.loc[0, 'rmssd_bbi']))

    def test_fixed_window_series_is_per_user(self):
        bbi = pd.DataFrame({
            'user_id': ['a'] * 5 + ['b'] * 5,
            'timestamp': list(pd.date_range('2026-06-01 09:00:00', periods=5, freq='s')) * 2,
            'bbi_ms': [800, 810, 790, 805, 795] * 2,
        })
        result = compute_rmssd_5min_series(bbi, min_beats=5)
        self.assertEqual(set(result['user_id']), {'a', 'b'})
        self.assertEqual(result['count'].max(), 5)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    unittest.main(verbosity=2)
