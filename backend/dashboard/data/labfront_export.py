"""Read a Labfront export folder for the weekly report.

The export is one folder per participant, named by study ID and a hash
(``RS01_8a0a989b``), with one subfolder per Garmin stream and one CSV per day
(``260921_garmin-connect-stress_RS01_8a0a989b.csv``). Each CSV opens with a
``Header Length,N`` line followed by N preamble lines, and only then the column
header.

The report reads these files directly and never loads them into HeartRateSample
or StressSample. Those tables feed the live decision engine: _evaluate_user
stamps the newest sample onto every decision as hr_at_trigger and
stress_at_trigger, so a back-loaded export would put a weeks-old reading on
every new decision until real ingest exists.

Garmin writes stress as -1 (too little data) and -2 (too much motion); those are
codes, not scores, and are dropped. Coverage is read from every row regardless,
because a coded row still proves the device synced up to that moment.
"""

import csv
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path

HEART_RATE = 'garmin-connect-daily-heart-rate'
STRESS = 'garmin-connect-stress'
STREAM_COLUMNS = {HEART_RATE: 'beatsPerMinute', STRESS: 'stressLevel'}
TIMESTAMP_COLUMN = 'unixTimestampInMs'
HEADER_LENGTH_KEY = 'Header Length'
FILE_DATE_FORMAT = '%y%m%d'


def study_id_of(folder):
    return Path(folder).name.split('_', 1)[0].strip().upper()


def participant_folders(root):
    return {
        study_id_of(path): path
        for path in sorted(Path(root).iterdir())
        if path.is_dir() and not path.name.startswith('.')
    }


def _file_date(path):
    try:
        return datetime.strptime(path.name[:6], FILE_DATE_FORMAT).date()
    except ValueError:
        return None


def _stream_files(folder, stream):
    return sorted((Path(folder) / stream).glob('*.csv'))


def _rows(path):
    with open(path, newline='', encoding='utf-8-sig') as handle:
        key, _, value = handle.readline().partition(',')
        if key.strip() != HEADER_LENGTH_KEY:
            raise ValueError(f'{path.name}: expected a "{HEADER_LENGTH_KEY}" first line')
        for _ in range(int(value)):
            handle.readline()
        yield from csv.DictReader(handle)


def _moment(row):
    try:
        return datetime.fromtimestamp(int(row[TIMESTAMP_COLUMN]) / 1000, tz=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def read_stream(folder, stream, start=None, end=None):
    """Valid samples as two parallel sorted lists, (times, values).

    Only files dated within a day of [start, end] are opened, so a weekly report
    does not parse the whole export. Times are aware UTC.
    """
    column = STREAM_COLUMNS[stream]
    samples = {}
    for path in _stream_files(folder, stream):
        day = _file_date(path)
        if day is not None and start is not None and day < start - timedelta(days=1):
            continue
        if day is not None and end is not None and day > end + timedelta(days=1):
            continue
        for row in _rows(path):
            moment = _moment(row)
            try:
                value = float(row[column])
            except (KeyError, TypeError, ValueError):
                continue
            if moment is None or value < 0:
                continue
            samples[moment] = value
    times = sorted(samples)
    return times, [samples[moment] for moment in times]


def values_between(stream, start, end):
    times, values = stream
    return values[bisect_left(times, start):bisect_right(times, end)]


def _first_or_last(files, last):
    for path in (reversed(files) if last else files):
        moments = [moment for moment in map(_moment, _rows(path)) if moment is not None]
        if moments:
            return max(moments) if last else min(moments)
    return None


def coverage(folder):
    """(first, last) sample time across the heart-rate and stress streams, or None."""
    firsts, lasts = [], []
    for stream in STREAM_COLUMNS:
        files = _stream_files(folder, stream)
        first, last = _first_or_last(files, last=False), _first_or_last(files, last=True)
        if first is not None:
            firsts.append(first)
            lasts.append(last)
    if not firsts:
        return None
    return min(firsts), max(lasts)
