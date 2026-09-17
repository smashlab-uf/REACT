import logging
from pathlib import Path
import tempfile
import unittest

import pandas as pd
from pandas.testing import assert_series_equal

from decision_engine.decision_engine import (
    apply_decision_rules,
    attach_rmssd_series_to_decisions,
    attach_rmssd_to_decisions,
    calculate_mssd,
    classify_rmssd,
    compute_rmssd_5min_series,
    compute_rmssd_from_bbi,
    load_bbi_csv,
    merge_hr_to_prompts,
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


    def test_min_beats_not_met_leaves_null_not_zero(self):
        decisions = pd.DataFrame([{
            'user_id': 'user-1',
            'timestamp': pd.Timestamp('2026-06-01 09:05:00'),
            'ema': 4,
            'send_prompt': True,
            'decision_reason': 'prompt sent',
        }])
        bbi = pd.DataFrame({
            'user_id': ['user-1'] * 5,
            'timestamp': pd.date_range('2026-06-01 09:00:00', periods=5, freq='s'),
            'bbi_ms': [800, 810, 790, 805, 795],
        })

        result = attach_rmssd_to_decisions(decisions, bbi, min_beats=6)

        self.assertEqual(result.loc[0, 'rmssd_count'], 5)
        self.assertTrue(pd.isna(result.loc[0, 'rmssd_bbi']))
        self.assertTrue(pd.isna(result.loc[0, 'hrv_class']))

    def test_first_window_has_no_baseline_so_no_class(self):
        decisions = pd.DataFrame([{
            'user_id': 'user-1',
            'timestamp': pd.Timestamp('2026-06-01 09:05:00'),
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

        self.assertFalse(pd.isna(result.loc[0, 'rmssd_bbi']))
        self.assertTrue(pd.isna(result.loc[0, 'rmssd_baseline']))
        self.assertTrue(pd.isna(result.loc[0, 'hrv_class']))

    def test_tz_aware_decision_frame_is_accepted(self):
        decisions = pd.DataFrame([{
            'user_id': 'user-1',
            'timestamp': pd.Timestamp('2026-06-01 09:05:00', tz='UTC'),
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

        self.assertEqual(result.loc[0, 'rmssd_count'], 5)
        self.assertFalse(pd.isna(result.loc[0, 'rmssd_bbi']))

    def test_class_cutoffs_are_exclusive_at_the_boundary(self):
        self.assertEqual(classify_rmssd(80.0, 100.0)[1], 'Balanced')
        self.assertEqual(classify_rmssd(120.0, 100.0)[1], 'Balanced')
        self.assertEqual(classify_rmssd(79.0, 100.0)[1], 'Low')
        self.assertEqual(classify_rmssd(121.0, 100.0)[1], 'High')
        self.assertTrue(pd.isna(classify_rmssd(80.0, float('nan'))[1]))
        self.assertTrue(pd.isna(classify_rmssd(80.0, 0.0)[1]))


class HrvSeriesAnnotationTests(unittest.TestCase):

    def _hrv(self, values, start='2026-06-01 09:00:00'):
        return pd.DataFrame({
            'user_id': ['user-1'] * len(values),
            'timestamp': pd.date_range(start, periods=len(values), freq='5min'),
            'rmssd_ms': values,
        })

    def _decision(self, timestamp):
        return pd.DataFrame([{
            'user_id': 'user-1',
            'timestamp': pd.Timestamp(timestamp),
            'ema': 4,
            'send_prompt': True,
            'decision_reason': 'prompt sent',
        }])

    def test_annotation_does_not_change_decision_columns(self):
        result = attach_rmssd_series_to_decisions(
            self._decision('2026-06-01 09:20:00'), self._hrv([100, 100, 100, 50])
        )

        self.assertTrue(result.loc[0, 'send_prompt'])
        self.assertEqual(result.loc[0, 'decision_reason'], 'prompt sent')

    def test_uses_the_latest_strictly_prior_sample(self):
        # 09:20 stamps the interval starting at 09:20, which covers the five
        # minutes after the decision, so it must not be read.
        hrv = pd.DataFrame({
            'user_id': ['user-1', 'user-1'],
            'timestamp': pd.to_datetime(['2026-06-01 09:15:00', '2026-06-01 09:20:00']),
            'rmssd_ms': [50, 999],
        })

        result = attach_rmssd_series_to_decisions(self._decision('2026-06-01 09:20:00'), hrv)

        self.assertEqual(result.loc[0, 'rmssd_ms'], 50)

    def test_baseline_window_bounds_the_history(self):
        decision = self._decision('2026-06-01 09:25:00')
        hrv = self._hrv([100, 100, 100, 50, 70])

        unbounded = attach_rmssd_series_to_decisions(decision, hrv)
        bounded = attach_rmssd_series_to_decisions(decision, hrv, baseline_window=2)

        self.assertEqual(unbounded.loc[0, 'rmssd_baseline'], 100)
        self.assertEqual(unbounded.loc[0, 'hrv_class'], 'Low')
        self.assertEqual(bounded.loc[0, 'rmssd_baseline'], 75)
        self.assertEqual(bounded.loc[0, 'hrv_class'], 'Balanced')

    def test_stale_hrv_is_not_attached(self):
        result = attach_rmssd_series_to_decisions(
            self._decision('2026-06-01 13:00:00'), self._hrv([100, 100, 100, 50])
        )

        self.assertTrue(pd.isna(result.loc[0, 'rmssd_ms']))
        self.assertTrue(pd.isna(result.loc[0, 'hrv_class']))

    def test_tz_aware_decision_frame_is_accepted(self):
        decision = self._decision('2026-06-01 09:25:00')
        decision['timestamp'] = decision['timestamp'].dt.tz_localize('UTC')

        result = attach_rmssd_series_to_decisions(decision, self._hrv([100, 100, 100, 50, 70]))

        self.assertEqual(result.loc[0, 'rmssd_ms'], 70)

    def test_unknown_participant_is_left_unannotated(self):
        hrv = self._hrv([100, 100, 100, 50])
        hrv['user_id'] = 'someone-else'

        result = attach_rmssd_series_to_decisions(self._decision('2026-06-01 09:20:00'), hrv)

        self.assertTrue(pd.isna(result.loc[0, 'rmssd_ms']))


class BbiLoadingTests(unittest.TestCase):

    CSV = (
        "projectId,projectName,participantId\n"
        "p-1,REACT,P-001\n"
        "\n"
        "timezone,isoDate,bbi,confidence\n"
        "America/New_York,2026-06-01T13:00:00Z,800,2\n"
        "America/New_York,2026-06-01T13:00:01Z,810,0\n"
        "America/New_York,2026-06-01T13:00:02Z,90,2\n"
        "America/New_York,2026-06-01T13:00:03Z,5000,2\n"
        "America/New_York,2026-06-01T13:00:04Z,790,2\n"
    )

    def _write(self):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8"
        )
        handle.write(self.CSV)
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def test_preamble_filters_and_participant_id(self):
        bbi = load_bbi_csv(self._write())

        self.assertEqual(list(bbi.columns), ['user_id', 'timestamp', 'bbi_ms', 'confidence'])
        self.assertEqual(list(bbi['bbi_ms']), [800, 790])
        self.assertEqual(set(bbi['user_id']), {'P-001'})
        self.assertIsNone(bbi['timestamp'].dt.tz)

    def test_physiological_bounds_are_configurable(self):
        bbi = load_bbi_csv(self._write(), min_bbi_ms=50, max_bbi_ms=6000)

        self.assertEqual(list(bbi['bbi_ms']), [800, 90, 5000, 790])


class HrMergeTests(unittest.TestCase):

    def test_bpm_column_and_mixed_awareness_are_accepted(self):
        ema = pd.DataFrame({
            'user_id': [1, 1],
            'timestamp': pd.to_datetime(
                ['2026-06-01 09:05:00', '2026-06-01 12:05:00']
            ).tz_localize('UTC'),
            'ema': [4, 5],
        })
        heart_rate = pd.DataFrame({
            'user_id': [1, 1],
            'timestamp': pd.to_datetime(['2026-06-01 09:00:00', '2026-06-01 12:00:00']),
            'bpm': [70, 90],
        })

        merged = merge_hr_to_prompts(ema, heart_rate)

        self.assertEqual(list(merged['hr']), [70, 90])
        self.assertIsNotNone(merged['timestamp'].dt.tz)

    def test_samples_outside_tolerance_are_not_carried_forward(self):
        ema = pd.DataFrame({
            'user_id': [1],
            'timestamp': pd.to_datetime(['2026-06-01 12:00:00']),
            'ema': [4],
        })
        heart_rate = pd.DataFrame({
            'user_id': [1],
            'timestamp': pd.to_datetime(['2026-06-01 09:00:00']),
            'bpm': [70],
        })

        merged = merge_hr_to_prompts(ema, heart_rate)

        self.assertTrue(pd.isna(merged.loc[0, 'hr']))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    unittest.main(verbosity=2)
