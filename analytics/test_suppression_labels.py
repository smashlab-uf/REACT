"""Pure-pandas tests for how the analytics package labels suppressed decision
points (run-in period, distress override). No database: run with

    cd analytics && python3 -m unittest test_suppression_labels
"""

import inspect
import sys
import types
import unittest

import matplotlib

matplotlib.use('Agg')

try:
    import seaborn  # noqa: F401
except ModuleNotFoundError:
    sys.modules['seaborn'] = types.ModuleType('seaborn')

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

import scripts  # noqa: E402
from monitor_app import timeline  # noqa: E402


def _decisions(rows):
    frame = pd.DataFrame(rows)
    frame['user_id'] = 1
    frame['observed_mssd'] = 1.0
    frame['randomization_probability'] = 0.5
    frame['triggered_at'] = pd.date_range('2026-09-20', periods=len(frame), freq='h', tz='UTC')
    return frame


SUPPRESSED_RUN_IN = dict(trigger_reason='run-in period', suppression_reason='run_in',
                         randomization_draw=None, send_prompt=False)
SUPPRESSED_DISTRESS = dict(trigger_reason='distress override (baseline)',
                           suppression_reason='distress_baseline',
                           randomization_draw=None, send_prompt=False)
ORDINARY_INELIGIBLE = dict(trigger_reason='cooldown active', suppression_reason='',
                           randomization_draw=None, send_prompt=False)
SENT = dict(trigger_reason='prompt sent', suppression_reason='',
            randomization_draw=0.2, send_prompt=True)


class DecisionCategoryTests(unittest.TestCase):

    def test_run_in_and_distress_reasons_get_their_own_categories(self):
        self.assertEqual(scripts._decision_category('run-in period'), 'Suppressed (run-in)')
        for reason in ('distress override (baseline)', 'distress override (momentary)'):
            self.assertEqual(scripts._decision_category(reason), 'Suppressed (distress)')

    def test_ordinary_categories_are_unchanged(self):
        self.assertEqual(scripts._decision_category('cooldown active'), 'Cooldown blocked')
        self.assertEqual(scripts._decision_category('prompt sent'), 'Sent / randomized')
        self.assertEqual(scripts._decision_category('something new'), 'Other')


class StageAuditTests(unittest.TestCase):

    def _all_row(self, frame):
        result = scripts.audit_decision_stages(frame)
        return result[result['user_id'] == 'ALL'].iloc[0]

    def test_suppressed_decisions_are_counted_separately(self):
        frame = _decisions([SUPPRESSED_RUN_IN, SUPPRESSED_DISTRESS, ORDINARY_INELIGIBLE, SENT])
        row = self._all_row(frame)
        self.assertEqual(row['suppressed'], 2)
        self.assertEqual(row['total_decision_points'], 4)
        self.assertEqual(row['eligible'], 1)
        self.assertEqual(row['not_sent'], 3)

    def test_a_frame_without_the_column_reports_zero_not_an_error(self):
        frame = _decisions([ORDINARY_INELIGIBLE, SENT]).drop(columns=['suppression_reason'])
        self.assertEqual(self._all_row(frame)['suppressed'], 0)

    def test_none_and_empty_reasons_are_not_suppressed(self):
        frame = _decisions([dict(ORDINARY_INELIGIBLE, suppression_reason=None), SENT])
        self.assertEqual(self._all_row(frame)['suppressed'], 0)


class TrajectoryPlotTests(unittest.TestCase):

    def tearDown(self):
        plt.close('all')

    def _points_by_label(self, frame):
        figure = scripts.plot_participant_mssd_trajectory(frame)
        return {c.get_label(): len(c.get_offsets()) for c in figure.axes[0].collections}

    def test_suppressed_points_are_plotted_as_suppressed_not_ineligible(self):
        counts = self._points_by_label(
            _decisions([SUPPRESSED_RUN_IN, SUPPRESSED_DISTRESS, ORDINARY_INELIGIBLE, SENT]))
        self.assertEqual(counts['suppressed'], 2)
        self.assertEqual(counts['ineligible'], 1)
        self.assertEqual(counts['sent'], 1)

    def test_older_frames_without_the_column_fall_back_to_the_trigger_reason(self):
        frame = _decisions([SUPPRESSED_RUN_IN, ORDINARY_INELIGIBLE]).drop(columns=['suppression_reason'])
        counts = self._points_by_label(frame)
        self.assertEqual(counts['suppressed'], 1)
        self.assertEqual(counts['ineligible'], 1)


class LoaderTests(unittest.TestCase):

    def test_the_jitai_loader_selects_the_suppression_reason(self):
        self.assertIn('"suppression_reason"', inspect.getsource(scripts.load_jitai_log))


class MonitorOutcomeTests(unittest.TestCase):

    def _decision_outcome(self, **overrides):
        event = {
            'kind': 'decision', 'at': pd.Timestamp('2026-09-20 10:00', tz='UTC'),
            'eligible': False, 'send_prompt': False, 'trigger_reason': 'cooldown active',
            'observed_mssd': 1.0, 'threshold_at_decision': 2.0, 'threshold_source': 'engine',
            'randomization_draw': None, 'decision_point_id': 'ema_1',
        }
        event.update(overrides)
        day = {'day_start': pd.Timestamp('2026-09-20 00:00', tz='UTC'),
               'local_date': '2026-09-20', 'events': [event]}
        return timeline.decision_frame(day).iloc[0]['outcome']

    def test_suppression_reason_labels_the_outcome(self):
        self.assertEqual(self._decision_outcome(suppression_reason='run_in', trigger_reason='run-in period'),
                         'suppressed (run-in)')
        for reason in ('distress_baseline', 'distress_momentary'):
            self.assertEqual(self._decision_outcome(suppression_reason=reason,
                                           trigger_reason='distress override (baseline)'),
                             'suppressed (distress)')

    def test_an_older_payload_without_the_reason_is_labeled_from_the_trigger_text(self):
        self.assertEqual(self._decision_outcome(trigger_reason='run-in period'), 'suppressed (run-in)')
        self.assertEqual(self._decision_outcome(trigger_reason='distress override (momentary)'),
                         'suppressed (distress)')

    def test_ordinary_outcomes_are_unchanged(self):
        self.assertEqual(self._decision_outcome(suppression_reason=None), 'cooldown')
        self.assertEqual(self._decision_outcome(eligible=True, send_prompt=True, trigger_reason='prompt sent'),
                         'eligible, sent')


if __name__ == '__main__':
    unittest.main()
