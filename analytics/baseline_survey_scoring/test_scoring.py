"""Pure-pandas tests for the Section 11 safety signals added to scoring.py
and section11.py. No Django: run with

    cd analytics/baseline_survey_scoring && python3 -m unittest test_scoring
"""

import io
import unittest
from contextlib import redirect_stdout

import numpy as np
import pandas as pd

import definitions as D
import qualtrics
import scoring
from section11 import REQUIRED_SCREEN_COLUMNS, section11_signals


def _row(**overrides):
    row = {column: np.nan for column in D.ALL_SCORING_COLUMNS}
    row[D.GENDER] = 'other'
    row.update(overrides)
    return row


def _phq9(total_besides_last, last):
    """PHQ9 items summing to `total_besides_last` across items 1-8, item 9 = `last`."""
    items = {D.PHQ9[i]: 0 for i in range(8)}
    items[D.PHQ9[0]] = total_besides_last
    items[D.PHQ9[-1]] = last
    return items


class PHQ9SafetyFlagsTests(unittest.TestCase):

    def _score(self, **overrides):
        frame = scoring.ensure_columns(pd.DataFrame([_row(**overrides)]), list(D.ALL_SCORING_COLUMNS))
        return scoring.score_phq9(frame)

    def test_self_harm_item_above_zero_flags(self):
        out = self._score(**_phq9(0, 1))
        self.assertTrue(out['phq9_self_harm_positive'].iloc[0])

    def test_self_harm_item_at_zero_does_not_flag(self):
        out = self._score(**_phq9(0, 0))
        self.assertFalse(out['phq9_self_harm_positive'].iloc[0])

    def test_self_harm_flag_is_independent_of_total_severity(self):
        # High total, self-harm item itself at 0: must not flag on total alone.
        items = {D.PHQ9[i]: 3 for i in range(8)}
        items[D.PHQ9[-1]] = 0
        out = self._score(**items)
        self.assertEqual(out['phq9_total'].iloc[0], 24)
        self.assertFalse(out['phq9_self_harm_positive'].iloc[0])

    def test_severity_below_moderately_severe_does_not_flag(self):
        items = {D.PHQ9[i]: 1 for i in range(8)}  # total 8, item 9 excluded
        items[D.PHQ9[-1]] = 0
        out = self._score(**items)
        self.assertEqual(out['phq9_total'].iloc[0], 8)
        self.assertFalse(out['phq9_severity_alert'].iloc[0])

    def test_severity_at_moderately_severe_cutoff_flags(self):
        items = {D.PHQ9[i]: 2 for i in range(7)}  # 14
        items[D.PHQ9[7]] = 1  # +1 = 15
        items[D.PHQ9[-1]] = 0
        out = self._score(**items)
        self.assertEqual(out['phq9_total'].iloc[0], 15)
        self.assertTrue(out['phq9_severity_alert'].iloc[0])

    def test_missing_self_harm_item_is_na_not_false(self):
        frame = scoring.ensure_columns(pd.DataFrame([_row()]), list(D.ALL_SCORING_COLUMNS))
        out = scoring.score_phq9(frame)
        self.assertTrue(pd.isna(out['phq9_self_harm_positive'].iloc[0]))


class PGSISafetyFlagTests(unittest.TestCase):

    def _score(self, total):
        items = {D.PGSI[i]: 0 for i in range(9)}
        for i in range(total):
            items[D.PGSI[i]] = 1
        frame = scoring.ensure_columns(pd.DataFrame([_row(**items)]), list(D.ALL_SCORING_COLUMNS))
        return scoring.score_pgsi(frame)

    def test_below_problem_gambling_cutoff_does_not_flag(self):
        out = self._score(7)
        self.assertEqual(out['pgsi_band'].iloc[0], 'moderate risk')
        self.assertFalse(out['pgsi_problem_gambling'].iloc[0])

    def test_at_problem_gambling_cutoff_flags(self):
        out = self._score(8)
        self.assertEqual(out['pgsi_band'].iloc[0], 'problem gambling')
        self.assertTrue(out['pgsi_problem_gambling'].iloc[0])


class Section11SignalsTests(unittest.TestCase):

    def test_no_signals_is_an_empty_list_not_nan(self):
        frame = pd.DataFrame([_row(**_phq9(0, 0))])
        scores, _ = scoring.score_participants(frame)
        signals = section11_signals(scores)
        self.assertEqual(signals.iloc[0], [])

    def test_multiple_positive_screens_all_appear(self):
        items = _phq9(0, 1)
        items.update({D.SCOFF[i]: 1 for i in range(5)})  # SCOFF total 5 >= cutoff 2
        frame = pd.DataFrame([_row(**items)])
        scores, _ = scoring.score_participants(frame)
        signals = section11_signals(scores)
        self.assertEqual(sorted(signals.iloc[0]), ['phq9_self_harm', 'scoff'])

    def test_asrs_never_appears_as_a_section11_signal(self):
        # ADHD screen positive should never leak into the distress-override codes.
        items = _phq9(0, 0)
        items.update({D.ASRS[i]: 3 for i in range(6)})
        frame = pd.DataFrame([_row(**items)])
        scores, _ = scoring.score_participants(frame)
        signals = section11_signals(scores)
        self.assertNotIn('asrs', str(SIGNAL_ANY := signals.iloc[0]))
        self.assertEqual(signals.iloc[0], [])


class MissingItemTests(unittest.TestCase):

    def _scores(self, **overrides):
        frame = pd.DataFrame([_row(**overrides)])
        scores, _ = scoring.score_participants(frame)
        return scores

    def test_one_missing_phq9_item_leaves_severity_unflagged_not_false(self):
        items = {D.PHQ9[i]: 3 for i in range(8)}
        items[D.PHQ9[2]] = np.nan
        items[D.PHQ9[-1]] = 0
        scores = self._scores(**items)
        self.assertTrue(pd.isna(scores['phq9_total'].iloc[0]))
        self.assertTrue(pd.isna(scores['phq9_severity_alert'].iloc[0]))
        self.assertEqual(section11_signals(scores).iloc[0], [])

    def test_a_missing_self_harm_item_yields_no_code(self):
        items = {D.PHQ9[i]: 0 for i in range(8)}
        items[D.PHQ9[-1]] = np.nan
        self.assertEqual(section11_signals(self._scores(**items)).iloc[0], [])


class ExactCutoffTests(unittest.TestCase):

    def _scores(self, **overrides):
        scores, _ = scoring.score_participants(pd.DataFrame([_row(**overrides)]))
        return scores.iloc[0]

    def test_phq9_total_14_is_below_and_15_is_at_the_severity_cutoff(self):
        below = {D.PHQ9[i]: 2 for i in range(7)}
        below[D.PHQ9[-1]] = 0
        at = dict(below, **{D.PHQ9[7]: 1})
        below[D.PHQ9[7]] = 0
        self.assertEqual(self._scores(**below)['phq9_total'], 14)
        self.assertFalse(self._scores(**below)['phq9_severity_alert'])
        self.assertEqual(self._scores(**at)['phq9_total'], 15)
        self.assertTrue(self._scores(**at)['phq9_severity_alert'])

    def test_self_harm_flags_at_every_nonzero_response(self):
        for value, expected in ((0, False), (1, True), (2, True), (3, True)):
            with self.subTest(value=value):
                row = self._scores(**_phq9(0, value))
                self.assertEqual(row['phq9_self_harm_positive'], expected)

    def test_scoff_flags_at_two_not_one(self):
        one = {D.SCOFF[0]: 1, **{D.SCOFF[i]: 0 for i in range(1, 5)}}
        two = dict(one, **{D.SCOFF[1]: 1})
        self.assertFalse(self._scores(**one)['scoff_positive'])
        self.assertTrue(self._scores(**two)['scoff_positive'])

    def test_hunger_flags_at_one_endorsed_item_not_zero(self):
        none = {D.HUNGER_VITAL_SIGN[0]: 0, D.HUNGER_VITAL_SIGN[1]: 0}
        one = dict(none, **{D.HUNGER_VITAL_SIGN[0]: 1})
        self.assertFalse(self._scores(**none)['hunger_positive'])
        self.assertTrue(self._scores(**one)['hunger_positive'])

    def test_audit_c_cutoff_is_4_for_men_and_3_for_everyone_else(self):
        def audit(total, gender):
            items = {D.AUDIT_C[0]: total, D.AUDIT_C[1]: 0, D.AUDIT_C[2]: 0, D.GENDER: gender}
            return self._scores(**items)['audit_c_alert']
        self.assertFalse(audit(3, 'man'))
        self.assertTrue(audit(4, 'man'))
        self.assertFalse(audit(2, 'woman'))
        self.assertTrue(audit(3, 'woman'))
        self.assertTrue(audit(3, np.nan))


class ReadExportTests(unittest.TestCase):

    def _csv(self, header, rows, bom=False):
        text = ','.join(header) + '\n' + '\n'.join(','.join(str(v) for v in r) for r in rows) + '\n'
        return io.BytesIO((('\ufeff' if bom else '') + text).encode('utf-8'))

    def test_reads_an_uploaded_file_like_object_without_touching_disk(self):
        source = self._csv(['participant_id', 'phq9_1'], [[7, 2]])
        frame, diagnostics = qualtrics.read_export(source)
        self.assertEqual(frame['participant_id'].tolist(), ['7'])
        self.assertEqual(frame['phq9_1'].tolist(), [2.0])

    def test_a_byte_order_mark_does_not_garble_the_first_header(self):
        frame, _ = qualtrics.read_export(self._csv(['participant_id', 'phq9_1'], [[7, 2]], bom=True))
        self.assertIn('participant_id', frame.columns)

    def test_diagnostics_list_missing_scoring_items_and_unrecognized_columns(self):
        source = self._csv(['participant_id', 'phq9_1', 'some_other_question'], [[7, 2, 'x']])
        _, diagnostics = qualtrics.read_export(source)
        self.assertIn('phq9_2', diagnostics['absent_columns'])
        self.assertNotIn('phq9_1', diagnostics['absent_columns'])
        self.assertEqual(diagnostics['unmatched_headers'], ['some_other_question'])

    def test_unknown_response_labels_are_reported_per_column(self):
        source = self._csv(['participant_id', 'phq9_1'], [[7, 'Sometimes maybe']])
        _, diagnostics = qualtrics.read_export(source)
        self.assertEqual(diagnostics['unknown_labels'], {'phq9_1': ['Sometimes maybe']})

    def test_load_export_still_prints_its_report_and_returns_the_frame(self):
        import tempfile, pathlib
        path = pathlib.Path(tempfile.mkdtemp()) / 'export.csv'
        path.write_bytes(self._csv(['participant_id', 'phq9_1'], [[7, 2]]).getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            frame = qualtrics.load_export(path)
        self.assertEqual(frame['phq9_1'].tolist(), [2.0])
        self.assertIn('loaded 1 rows', out.getvalue())
        self.assertIn('scoring items NOT found', out.getvalue())


class RequiredScreenColumnsTests(unittest.TestCase):

    def test_every_automated_section11_instrument_is_required(self):
        for group in (D.PHQ9, D.SCOFF, D.AUDIT_C, D.PGSI, D.HUNGER_VITAL_SIGN):
            self.assertTrue(set(group) <= set(REQUIRED_SCREEN_COLUMNS))

    def test_unrelated_instruments_are_not_required(self):
        self.assertFalse(set(D.ASRS) & set(REQUIRED_SCREEN_COLUMNS))


if __name__ == '__main__':
    unittest.main()
