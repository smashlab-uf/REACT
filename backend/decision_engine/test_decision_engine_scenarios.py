import logging
from pathlib import Path
import unittest

import pandas as pd
from pandas.testing import assert_series_equal

from decision_engine.decision_engine import apply_decision_rules, calculate_mssd


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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    unittest.main(verbosity=2)
