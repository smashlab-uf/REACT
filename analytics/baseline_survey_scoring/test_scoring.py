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


class ThreeRowHeaderTests(unittest.TestCase):
    """Qualtrics' three-row-header export: short internal codes as the literal
    column names, human-readable question text one row below, an ImportId JSON
    row below that, then data. read_export must match on the text row, not the
    short codes -- this is the real export shape, not the two-row shape the
    other ReadExportTests fixtures use."""

    def _csv(self, codes, texts, rows):
        import_row = [f'{{"ImportId":"{c}"}}' for c in codes]
        lines = [
            ','.join(codes),
            ','.join(f'"{t}"' if ',' in t else t for t in texts),
            ','.join(import_row),
        ]
        lines += [','.join(str(v) for v in r) for r in rows]
        return io.BytesIO(('\n'.join(lines) + '\n').encode('utf-8'))

    PHQ9_9_TEXT = 'Thoughts that you would be better off dead or of hurting yourself in some way'
    PHQ9_9_STEM_TEXT = (
        'Over the last 2 weeks, how often have you been bothered by any of the following problems? '
        '- Thoughts that you would be better off dead or of hurting yourself in some way'
    )

    def test_short_code_alone_would_not_match_but_the_text_row_does(self):
        source = self._csv(['Q0', 'Q1'], ['participant_id', self.PHQ9_9_TEXT], [[7, 1]])
        frame, diagnostics = qualtrics.read_export(source)
        self.assertIn('phq9_9', frame.columns)
        self.assertEqual(frame['phq9_9'].tolist(), [1.0])
        self.assertNotIn('Q1', frame.columns)

    def test_stem_prefixed_grid_text_also_resolves(self):
        source = self._csv(['Q0', 'Q1'], ['participant_id', self.PHQ9_9_STEM_TEXT], [[7, 3]])
        frame, _ = qualtrics.read_export(source)
        self.assertIn('phq9_9', frame.columns)
        self.assertEqual(frame['phq9_9'].tolist(), [3.0])

    def test_an_unmatched_column_keeps_its_short_code_name(self):
        source = self._csv(['Q0', 'Q1'], ['What is your participant ID? (ex. RS01)', self.PHQ9_9_TEXT],
                           [['RS01', 1]])
        frame, diagnostics = qualtrics.read_export(source)
        self.assertIn('Q0', frame.columns)
        self.assertIn('What is your participant ID? (ex. RS01)', diagnostics['unmatched_headers'])
        self.assertIn('phq9_9', frame.columns)

    def test_all_nine_phq9_items_resolve_from_a_real_shaped_export(self):
        items = [
            'Little interest or pleasure in doing things',
            'Feeling down, depressed, or hopeless',
            'Trouble falling or staying asleep, or sleeping too much',
            'Feeling tired or having little energy',
            'Poor appetite or overeating',
            'Feeling bad about yourself, or that you are a failure, or have let yourself or your '
            'family down',
            'Trouble concentrating on things, such as reading the newspaper or watching television',
            'Moving or speaking so slowly that other people could have noticed; or the opposite, '
            'being so fidgety or restless that you have been moving around a lot more than usual',
            self.PHQ9_9_TEXT,
        ]
        stem = 'Over the last 2 weeks, how often have you been bothered by any of the following problems?'
        codes = [f'Q{i}' for i in range(1, 10)]
        texts = [f'{stem} - {item}' for item in items]
        values = [0, 2, 2, 2, 0, 0, 1, 0, 1]
        source = self._csv(codes, texts, [values])
        frame, _ = qualtrics.read_export(source)
        for column in D.PHQ9:
            self.assertIn(column, frame.columns, column)
        self.assertEqual(frame['phq9_9'].tolist(), [1.0])


class UpdatedLiveSurveyWordingTests(unittest.TestCase):
    """scoff_3, pgsi_2, pgsi_7 and hunger_vital_sign_2 did not match a real
    export (confirmed 2026-09-29) -- the live survey's current wording for
    each, confirmed against that export, replaced the stale registered text
    2026-10-01. Pins the new wording so a future registration drift is caught
    the same way."""

    def test_scoff_3_matches_the_current_fourteen_pound_wording(self):
        header = 'Have you recently lost more than about 14 pounds in a 3-month period?'
        resolved, unmatched = qualtrics.resolve_columns([header])
        self.assertEqual(resolved.get(header), D.SCOFF[2])
        self.assertEqual(unmatched, [])

    def test_scoff_3_no_longer_matches_the_old_fifteen_pound_wording(self):
        header = 'Have you recently lost more than Fifteen pounds in a 3 month period?'
        resolved, unmatched = qualtrics.resolve_columns([header])
        self.assertNotIn(header, resolved)
        self.assertEqual(unmatched, [header])

    def test_pgsi_2_matches_the_current_wording_directly(self):
        header = 'Have you needed to gamble with larger amounts of money to get the same feeling of excitement?'
        resolved, unmatched = qualtrics.resolve_columns([header])
        self.assertEqual(resolved.get(header), D.PGSI[1])

    def test_pgsi_2_matches_via_the_grid_stem_fallback(self):
        header = ('Thinking about the last 12 months: - Have you needed to gamble with larger '
                   'amounts of money to get the same feeling of excitement?')
        resolved, unmatched = qualtrics.resolve_columns([header])
        self.assertEqual(resolved.get(header), D.PGSI[1])

    def test_pgsi_7_matches_the_shortened_current_wording(self):
        header = 'Have people criticized your betting or told you that you had a gambling problem?'
        resolved, unmatched = qualtrics.resolve_columns([header])
        self.assertEqual(resolved.get(header), D.PGSI[6])

    def test_hunger_vital_sign_2_matches_the_spelled_out_wording(self):
        header = ('Within the past 12 months the food we bought just did not last and we did not '
                   'have money to get more.')
        resolved, unmatched = qualtrics.resolve_columns([header])
        self.assertEqual(resolved.get(header), D.HUNGER_VITAL_SIGN[1])

    def test_a_real_shaped_export_with_current_wording_leaves_none_of_the_four_missing(self):
        codes = ['Q60', 'Q38e_2', 'Q38e_7', 'Q41']
        texts = [
            'Have you recently lost more than about 14 pounds in a 3-month period?',
            'Thinking about the last 12 months: - Have you needed to gamble with larger amounts '
            'of money to get the same feeling of excitement?',
            'Thinking about the last 12 months: - Have people criticized your betting or told '
            'you that you had a gambling problem?',
            'Within the past 12 months the food we bought just did not last and we did not have '
            'money to get more.',
        ]
        import_row = [f'{{"ImportId":"{c}"}}' for c in codes]
        lines = [
            ','.join(codes),
            ','.join(f'"{t}"' for t in texts),
            ','.join(import_row),
            '0,0,0,0',
        ]
        source = io.BytesIO(('\n'.join(lines) + '\n').encode('utf-8'))
        frame, _ = qualtrics.read_export(source)
        for column in (D.SCOFF[2], D.PGSI[1], D.PGSI[6], D.HUNGER_VITAL_SIGN[1]):
            self.assertIn(column, frame.columns, column)


class ResolveColumnsStemFallbackTests(unittest.TestCase):

    def test_a_stem_prefixed_header_resolves_via_the_tail(self):
        header = ('Respond to the following statements on a scale from 1=Extremely uncharacteristic '
                  'of me to 7=Extremely characteristic of me: - Given enough provocation, I may hit '
                  'another person.')
        resolved, unmatched = qualtrics.resolve_columns([header])
        self.assertEqual(resolved.get(header), D.BAQ[0])
        self.assertEqual(unmatched, [])

    def test_a_plain_header_with_no_stem_still_resolves_directly(self):
        header = 'Given enough provocation, I may hit another person.'
        resolved, unmatched = qualtrics.resolve_columns([header])
        self.assertEqual(resolved[header], D.BAQ[0])

    def test_a_non_item_header_with_two_dashes_is_not_falsely_matched(self):
        header = 'What is your gender identity? - Prefer to self describe - Text'
        resolved, unmatched = qualtrics.resolve_columns([header])
        self.assertEqual(unmatched, [header])


class MissingTokenNinetyNineTests(unittest.TestCase):

    def test_bare_99_in_a_scoring_column_is_missing_not_a_literal_score(self):
        header = ['participant_id'] + list(D.PHQ9)
        values = [7] + [0] * 8 + ['99']
        text = ','.join(header) + '\n' + ','.join(str(v) for v in values) + '\n'
        frame, _ = qualtrics.read_export(io.BytesIO(text.encode('utf-8')))
        out = scoring.score_phq9(scoring.ensure_columns(frame, list(D.ALL_SCORING_COLUMNS)))
        self.assertTrue(pd.isna(out['phq9_total'].iloc[0]))
        self.assertTrue(pd.isna(out['phq9_self_harm_positive'].iloc[0]))
        self.assertTrue(pd.isna(out['phq9_severity_alert'].iloc[0]))

    def test_99_does_not_leak_through_to_numeric_conversion(self):
        converted, unknown = qualtrics.to_numeric(pd.Series(['99', '1', '2']), None)
        self.assertTrue(pd.isna(converted.iloc[0]))
        self.assertEqual(converted.iloc[1:].tolist(), [1.0, 2.0])
        self.assertEqual(unknown, [])


class RequiredScreenColumnsTests(unittest.TestCase):

    def test_every_automated_section11_instrument_is_required(self):
        for group in (D.PHQ9, D.SCOFF, D.AUDIT_C, D.PGSI, D.HUNGER_VITAL_SIGN):
            self.assertTrue(set(group) <= set(REQUIRED_SCREEN_COLUMNS))

    def test_unrelated_instruments_are_not_required(self):
        self.assertFalse(set(D.ASRS) & set(REQUIRED_SCREEN_COLUMNS))


if __name__ == '__main__':
    unittest.main()
