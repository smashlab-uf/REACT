import csv
import json
import zipfile
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
from datetime import timezone as datetime_timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from app.models import (
    HRVSample,
    HeartRateSample,
    StressSample,
    User,
    WearableDevice,
    record_sync,
)


SOURCE = "garmin_labfront"

PARTICIPANT_COLUMNS = (
    "participant_id",
    "labfront_participant_id",
    "user_id",
    "subject_id",
    "study_id",
)
TIMESTAMP_COLUMNS = (
    "timestamp",
    "time",
    "datetime",
    "isodate",
    "iso_date",
    "start_time",
    "start_timestamp",
    "iso_time",
    "unixtimestampinms",
    "unix_timestamp_in_ms",
)
HEART_RATE_COLUMNS = ("beatsperminute", "beats_per_minute", "bpm", "heart_rate", "heartrate", "heart_rate_bpm", "hr")
STRESS_COLUMNS = ("stresslevel", "stress_score", "stress", "stress_level")
RMSSD_COLUMNS = ("hrv", "rmssd_ms", "rmssd", "hrv_rmssd", "rmssd_milliseconds")
BEAT_COUNT_COLUMNS = ("beat_count", "beats", "n_beats", "sample_count")

LABFRONT_IMPORT_PATH_MARKERS = {
    "heart_rate": (
        "daily_heart_rate",
        "heart_rate",
        "heartrate",
    ),
    "stress": (
        "garmin_stress_score",
        "device_stress_measurements",
        "stress_metrics",
        "stress",
    ),
    "hrv": (
        "garmin_connect_hrv_values",
        "hrv_values",
    ),
}

LABFRONT_SKIP_PATH_MARKERS = (
    # Raw beat-level data is retained as files and summarized before database load.
    "garmin_device_enhanced_bbi",
    "beat_to_beat",
    "bbi",
    # Nightly/aggregate exports are not part of the current EMA decision path.
    "garmin_connect_hrv_summary",
    "hrv_summary",
    "sleep_summary",
    "sleep_stages",
    "sleep_respiration",
    "sleep_nap",
    "activity_summary",
    "activity_details",
    "daily_summary",
    "move_iq",
    "epoch_data",
    "respiration_rate",
    "skin_temperature",
)


class Command(BaseCommand):
    help = "Import structured Labfront wearable export CSVs into REACT wearable tables."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to a Labfront export zip, CSV, or extracted folder.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report counts without writing database rows.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum rows to process per CSV, useful while testing a new export format.",
        )

    def handle(self, *args, **options):
        source_path = Path(options["path"]).expanduser()
        dry_run = options["dry_run"]
        limit = options["limit"]

        if not source_path.exists():
            raise CommandError(f"Path does not exist: {source_path}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run enabled. No database rows will be saved."))

        with extracted_export(source_path) as root:
            csv_paths = list_csv_files(root)
            if not csv_paths:
                raise CommandError(f"No CSV files found in {source_path}")

            totals = Counter()
            with transaction.atomic():
                for csv_path in csv_paths:
                    report = self.import_csv(csv_path, limit=limit, dry_run=dry_run)
                    totals.update(report)
                    self.print_file_report(csv_path, report)

                if dry_run:
                    transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Labfront import summary"))
        for key in sorted(totals):
            self.stdout.write(f"  {key}: {totals[key]}")

    def import_csv(self, csv_path, limit=None, dry_run=False):
        report = Counter(files_seen=1)
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader, metadata = read_labfront_csv(handle)
            if not reader.fieldnames:
                report["empty_files"] += 1
                return report

            columns = normalize_columns(reader.fieldnames)
            kind = detect_file_kind(csv_path, columns)
            if kind is None:
                report["unsupported_files"] += 1
                return report

            timestamp_col = first_present(columns, TIMESTAMP_COLUMNS)
            participant_col = first_present(columns, PARTICIPANT_COLUMNS)
            participant_ids = participant_candidates(metadata)
            if participant_col is None and not participant_ids:
                report["missing_participant"] += 1
                return report
            if timestamp_col is None:
                report["missing_required_columns"] += 1
                return report

            latest_timestamp = None
            sync_user = None
            samples_created = 0
            for index, raw_row in enumerate(reader, start=1):
                if limit is not None and index > limit:
                    report["limited"] += 1
                    break
                report["rows_seen"] += 1

                row = normalize_row(raw_row)
                row_participant = row.get(participant_col) if participant_col else None
                user = find_user(*(participant_ids + [row_participant]))
                timestamp = parse_export_datetime(row.get(timestamp_col), timestamp_col)
                if user is None:
                    report["unknown_participant"] += 1
                    continue
                if timestamp is None:
                    report["bad_timestamp"] += 1
                    continue

                try:
                    created, existing = self.write_sample(kind, row, user, timestamp, dry_run=dry_run)
                except (KeyError, TypeError, ValueError):
                    report["bad_value"] += 1
                    continue

                report[f"{kind}_created"] += created
                report[f"{kind}_existing"] += existing
                samples_created += created
                if latest_timestamp is None or timestamp > latest_timestamp:
                    latest_timestamp = timestamp
                    sync_user = user

            if latest_timestamp is not None and sync_user is not None:
                sync_written = update_device_sync(
                    sync_user,
                    latest_timestamp,
                    samples_written=samples_created,
                    dry_run=dry_run,
                )
                if sync_written:
                    report["sync_rows_created"] += 1

        return report

    def write_sample(self, kind, row, user, timestamp, dry_run=False):
        if kind == "heart_rate":
            bpm = int(float(row[first_present(row, HEART_RATE_COLUMNS)]))
            if dry_run:
                created = not HeartRateSample.objects.filter(
                    user=user, timestamp=timestamp, source=SOURCE
                ).exists()
                return int(created), int(not created)
            _, created = HeartRateSample.objects.get_or_create(
                user=user,
                timestamp=timestamp,
                source=SOURCE,
                defaults={"bpm": bpm},
            )
            return int(created), int(not created)

        if kind == "stress":
            stress_score = int(float(row[first_present(row, STRESS_COLUMNS)]))
            if stress_score < 0:
                return 0, 0
            if dry_run:
                created = not StressSample.objects.filter(
                    user=user, timestamp=timestamp, source=SOURCE
                ).exists()
                return int(created), int(not created)
            _, created = StressSample.objects.get_or_create(
                user=user,
                timestamp=timestamp,
                source=SOURCE,
                defaults={"stress_score": stress_score},
            )
            return int(created), int(not created)

        if kind == "hrv":
            return write_hrv_samples(row, user, timestamp, dry_run=dry_run)

        raise ValueError(f"Unsupported file kind: {kind}")

    def print_file_report(self, csv_path, report):
        if report["unsupported_files"]:
            self.stdout.write(f"Skipped unsupported CSV: {csv_path.name}")
            return
        parts = ", ".join(f"{key}={value}" for key, value in sorted(report.items()) if value)
        self.stdout.write(f"{csv_path.name}: {parts}")


def write_hrv_samples(row, user, timestamp, dry_run=False):
    hrv_col = first_present(row, RMSSD_COLUMNS)
    hrv_values = parse_hrv_values(row[hrv_col], timestamp)
    if not hrv_values:
        return 0, 0

    created_count = 0
    existing_count = 0
    beat_col = first_present(row, BEAT_COUNT_COLUMNS)
    beat_count = int(float(row[beat_col])) if beat_col and row.get(beat_col) else 0

    for sample_timestamp, rmssd_ms in hrv_values:
        if dry_run:
            created = not HRVSample.objects.filter(
                user=user, timestamp=sample_timestamp, source=SOURCE
            ).exists()
        else:
            _, created = HRVSample.objects.get_or_create(
                user=user,
                timestamp=sample_timestamp,
                source=SOURCE,
                defaults={"rmssd_ms": rmssd_ms, "beat_count": beat_count},
            )
        if created:
            created_count += 1
        else:
            existing_count += 1
    return created_count, existing_count


def parse_hrv_values(raw_value, base_timestamp):
    if raw_value in (None, ""):
        return []
    if isinstance(raw_value, (int, float)):
        return [(base_timestamp, float(raw_value))]

    text = str(raw_value).strip()
    try:
        return [(base_timestamp, float(text))]
    except ValueError:
        pass

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []

    samples = []
    if isinstance(parsed, dict):
        items = parsed.items()
    elif isinstance(parsed, list):
        items = enumerate(parsed)
    else:
        return []

    for offset, value in items:
        try:
            rmssd_ms = float(value)
            offset_seconds = int(float(offset))
        except (TypeError, ValueError):
            continue
        samples.append((base_timestamp + timedelta(seconds=offset_seconds), rmssd_ms))
    return samples


@contextmanager
def extracted_export(source_path):
    if source_path.is_dir():
        yield source_path
        return

    if source_path.suffix.lower() == ".csv":
        yield source_path
        return

    if not zipfile.is_zipfile(source_path):
        raise CommandError("Expected a .zip export, .csv file, or extracted folder.")

    with TemporaryDirectory() as temp_dir:
        with zipfile.ZipFile(source_path) as archive:
            archive.extractall(temp_dir)
        yield Path(temp_dir)


def list_csv_files(root):
    if root.is_file() and root.suffix.lower() == ".csv":
        return [root]
    return sorted(path for path in root.rglob("*.csv") if path.is_file())


def normalize_columns(fieldnames):
    return {normalize_key(name): name for name in fieldnames}


def normalize_row(raw_row):
    return {normalize_key(key): value.strip() if isinstance(value, str) else value for key, value in raw_row.items()}


def normalize_key(value):
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def read_labfront_csv(handle):
    """Return the data reader and Labfront's file-level participant metadata."""
    first_row = next(csv.reader(handle), None)
    if not first_row:
        return csv.DictReader([]), {}

    if normalize_key(first_row[0]) != "header_length":
        handle.seek(0)
        return csv.DictReader(handle), {}

    try:
        header_length = int(first_row[1])
    except (IndexError, TypeError, ValueError) as exc:
        raise CommandError("Invalid Labfront Header Length row.") from exc

    metadata_rows = [next(csv.reader(handle), []) for _ in range(max(header_length - 1, 0))]
    metadata = {}
    for keys, values in zip(metadata_rows, metadata_rows[1:]):
        normalized_keys = [normalize_key(key) for key in keys]
        if "participantid" not in normalized_keys and "participantinsignia" not in normalized_keys:
            continue
        metadata = {
            normalize_key(key): value.strip()
            for key, value in zip(keys, values)
            if key
        }
        break

    data_header = []
    row_reader = csv.reader(handle)
    for row in row_reader:
        if any(str(value).strip() for value in row):
            data_header = row
            break
    return csv.DictReader(handle, fieldnames=data_header), metadata


def participant_candidates(metadata):
    return [
        value
        for value in (
            metadata.get("participantid"),
            metadata.get("participantinsignia"),
        )
        if value
    ]


def first_present(mapping, candidates):
    for candidate in candidates:
        if candidate in mapping:
            return candidate
    return None


def detect_file_kind(path, columns):
    path_key = normalize_key("/".join(path.parts))
    if any(marker in path_key for marker in LABFRONT_SKIP_PATH_MARKERS):
        return None

    if (
        first_present(columns, HEART_RATE_COLUMNS)
        or any(marker in path_key for marker in LABFRONT_IMPORT_PATH_MARKERS["heart_rate"])
    ):
        return "heart_rate"
    if (
        first_present(columns, STRESS_COLUMNS)
        or any(marker in path_key for marker in LABFRONT_IMPORT_PATH_MARKERS["stress"])
    ):
        return "stress"
    if (
        first_present(columns, RMSSD_COLUMNS)
        or any(marker in path_key for marker in LABFRONT_IMPORT_PATH_MARKERS["hrv"])
    ):
        return "hrv"
    return None


def parse_export_datetime(value, column=None):
    if not value:
        return None
    if column in {"unixtimestampinms", "unix_timestamp_in_ms"}:
        try:
            return timezone.datetime.fromtimestamp(float(value) / 1000, tz=datetime_timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, datetime_timezone.utc)
    return parsed


def find_user(*participant_ids):
    for raw_id in participant_ids:
        participant_id = str(raw_id or "").strip()
        if not participant_id:
            continue

        device = (
            WearableDevice.objects
            .select_related("user")
            .filter(labfront_participant_id__iexact=participant_id)
            .first()
        )
        if device:
            return device.user

        user = User.objects.filter(study_id__iexact=participant_id).first()
        if user:
            return user
    return None


def update_device_sync(user, timestamp, samples_written=0, dry_run=False):
    device = WearableDevice.objects.filter(user=user).first()
    if device is None:
        return False
    if device.last_synced_at is not None and timestamp <= device.last_synced_at:
        return False
    if dry_run:
        return True
    device.last_synced_at = timestamp
    device.save(update_fields=["last_synced_at"])
    return record_sync(
        user,
        timestamp,
        "ingest",
        samples_written=samples_written,
    ) is not None
