import sys
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction

from app.models import DistressFlag, User


class BaselineImportError(ValueError):
    pass


@dataclass
class ImportReport:
    dry_run: bool
    rows: int = 0
    planned: list = field(default_factory=list)
    created: list = field(default_factory=list)
    skipped_existing: int = 0
    skipped_no_signal: int = 0
    unmatched: list = field(default_factory=list)
    ambiguous: list = field(default_factory=list)
    bad_identifier: list = field(default_factory=list)
    missing_screen_columns: list = field(default_factory=list)
    incomplete: list = field(default_factory=list)
    unmatched_headers: list = field(default_factory=list)
    unknown_labels: dict = field(default_factory=dict)
    refused: bool = False


def _scoring_modules():
    directory = str(settings.REPO_ROOT / 'analytics' / 'baseline_survey_scoring')
    if directory not in sys.path:
        sys.path.insert(0, directory)
    import qualtrics
    import scoring
    import section11
    return qualtrics, scoring, section11


def _coerce_user_id(identifier):
    try:
        return int(identifier)
    except (TypeError, ValueError):
        try:
            as_float = float(identifier)
        except (TypeError, ValueError):
            return None
        return int(as_float) if as_float.is_integer() else None


def import_baseline_flags(source, id_column='participant_id', id_field='user_id',
                          dry_run=False, require_complete_screens=False, on_create=None):
    qualtrics, scoring, section11 = _scoring_modules()

    frame, diagnostics = qualtrics.read_export(source)
    if id_column not in frame.columns:
        raise BaselineImportError(
            f'--id-column {id_column!r} not found in the export. '
            f'Columns present: {sorted(frame.columns)}'
        )

    report = ImportReport(dry_run=dry_run, rows=len(frame))
    report.unmatched_headers = diagnostics['unmatched_headers']
    report.unknown_labels = diagnostics['unknown_labels']
    present = set(frame.columns)
    report.missing_screen_columns = [
        column for column in section11.REQUIRED_SCREEN_COLUMNS if column not in present
    ]
    checkable = [column for column in section11.REQUIRED_SCREEN_COLUMNS if column in present]
    if checkable:
        blank = frame[checkable].isna().any(axis=1)
        report.incomplete = [str(frame.loc[idx, id_column]) for idx in frame.index[blank]]

    if report.missing_screen_columns and require_complete_screens and not dry_run:
        report.refused = True
        return report

    scores, _ = scoring.score_participants(frame)
    signals_by_row = section11.section11_signals(scores)

    plan = []
    for idx in frame.index:
        codes = signals_by_row.loc[idx]
        if not codes:
            report.skipped_no_signal += 1
            continue

        identifier = frame.loc[idx, id_column]
        if identifier is None or (isinstance(identifier, float) and identifier != identifier):
            report.bad_identifier.append((idx, codes))
            continue

        lookup_value = identifier
        if id_field == 'user_id':
            lookup_value = _coerce_user_id(identifier)
            if lookup_value is None:
                report.bad_identifier.append((identifier, codes))
                continue

        matches = list(User.objects.filter(**{id_field: lookup_value}))
        if not matches:
            report.unmatched.append((identifier, codes))
            continue
        if len(matches) > 1:
            report.ambiguous.append((identifier, codes))
            continue

        user = matches[0]
        if DistressFlag.objects.filter(user=user, source='baseline', signals=codes).exists():
            report.skipped_existing += 1
            continue
        plan.append((user, codes))

    report.planned = [
        {'user_id': user.user_id, 'email': user.email, 'signals': codes} for user, codes in plan
    ]
    if dry_run:
        return report

    with transaction.atomic():
        for user, codes in plan:
            flag = DistressFlag.objects.create(user=user, source='baseline', signals=codes)
            if on_create is not None:
                on_create(flag)
            report.created.append({'user_id': user.user_id, 'email': user.email, 'signals': codes})
    return report
