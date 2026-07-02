"""
Post synthetic CSV exports into the deployed Django backend through public APIs.

Flow:
  1. POST each row from app_user.csv to /user/.
  2. Map old CSV user IDs to the new backend user IDs.
  3. POST each user's wearable, heart-rate samples, and EMA rows to /telemetry/ingest/.

This script is intentionally API-only: it does not connect to Postgres directly.
"""

import argparse
import csv
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from http.client import RemoteDisconnected
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_API = "https://healthygatorsportfan-ab9271b02569.herokuapp.com"
DEFAULT_PASSWORD = "SyntheticPass123"


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def blank_to_none(value):
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None


def parse_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def iso_datetime(value):
    value = blank_to_none(value)
    if value is None:
        return None

    # CSVs use forms like "2026-06-01 00:00:00" and dates like "2026-06-11".
    if "T" in value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif len(value) == 10:
        parsed = datetime.fromisoformat(f"{value}T00:00:00+00:00")
    else:
        parsed = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def to_int(value):
    value = blank_to_none(value)
    return int(float(value)) if value is not None else None


def clamp(value, low, high):
    if value is None:
        return None
    return max(low, min(high, value))


def post_json(url, payload, dry_run=False, retries=3):
    if dry_run:
        return {"dry_run": True}

    body = json.dumps(payload).encode("utf-8")
    last_error = None
    for attempt in range(1, retries + 1):
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=120) as response:
                response_body = response.read().decode("utf-8")
                return json.loads(response_body) if response_body else {}
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8")
            # Retry server-side transient failures, fail fast on validation/client errors.
            if exc.code < 500 or attempt == retries:
                raise RuntimeError(f"POST {url} failed with {exc.code}: {error_body}") from exc
            last_error = RuntimeError(f"POST {url} failed with {exc.code}: {error_body}")
        except (URLError, RemoteDisconnected) as exc:
            if attempt == retries:
                raise RuntimeError(f"POST {url} failed: {exc}") from exc
            last_error = exc

        wait_seconds = 2 ** (attempt - 1)
        print(f"retrying POST {url} after error: {last_error} (attempt {attempt}/{retries})")
        time.sleep(wait_seconds)

    raise RuntimeError(f"POST {url} failed after {retries} attempts: {last_error}")


def chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def build_user_payload(row, run_label):
    email = blank_to_none(row.get("email"))
    if run_label:
        local, _, domain = email.partition("@")
        email = f"{local}.{run_label}@{domain}"

    return {
        "email": email,
        "password": blank_to_none(row.get("password")) or DEFAULT_PASSWORD,
        "first_name": blank_to_none(row.get("first_name")) or "Synthetic",
        "last_name": blank_to_none(row.get("last_name")) or "User",
        "birthdate": blank_to_none(row.get("birthdate")) or "2000-01-01",
        "gender": (blank_to_none(row.get("gender")) or "other").lower(),
        "height_feet": str(blank_to_none(row.get("height_feet")) or "0"),
        "height_inches": str(blank_to_none(row.get("height_inches")) or "0"),
        "goal_weight": str(blank_to_none(row.get("goal_weight")) or "0.0"),
        "goal_to_lose_weight": parse_bool(row.get("goal_to_lose_weight")),
        "goal_to_feel_better": parse_bool(row.get("goal_to_feel_better")),
    }


def build_wearable_payload(row, run_label=None):
    participant_id = blank_to_none(row.get("labfront_participant_id"))
    participant_id = participant_id or blank_to_none(row.get("fitbit_device_id"))
    if participant_id and run_label:
        participant_id = f"{participant_id}-{run_label}"
    return {
        "labfront_participant_id": participant_id,
        "device_name": blank_to_none(row.get("device_name")),
        "last_synced_at": iso_datetime(row.get("last_synced_at")),
        "is_active": parse_bool(row.get("is_active", "true")),
    }


def build_hr_payload(rows):
    samples = []
    for row in rows:
        bpm = to_int(row.get("bpm"))
        if bpm is None:
            continue
        samples.append({
            "timestamp": iso_datetime(row.get("timestamp")),
            "bpm": bpm,
            "source": blank_to_none(row.get("source")) or "synthetic_csv",
        })
    return samples


def build_ema_payload(rows):
    emas = []
    for index, row in enumerate(rows, start=1):
        mood = clamp(to_int(row.get("mood")), 1, 7)
        energy = clamp(to_int(row.get("energy")), 1, 7)
        stress = clamp(to_int(row.get("stress")), 1, 7)
        if mood is None and energy is None and stress is None:
            continue
        timestamp = iso_datetime(row.get("timestamp"))
        prompt_suffix = timestamp.replace(":", "").replace("-", "") if timestamp else str(index)
        emas.append({
            "prompt_id": f"SYNTH_EMA_{prompt_suffix}_{index}",
            "responded_at": timestamp,
            "status": "completed",
            "mood": mood,
            "energy": energy,
            "stress": stress,
        })
    return emas


def choose_csv_dir(explicit_dir):
    if explicit_dir:
        return Path(explicit_dir)

    base = Path(__file__).resolve().parent
    for candidate in (base, base / "csv_export", base / "__pycache__"):
        if (candidate / "app_user.csv").exists():
            return candidate
    return base


def main():
    parser = argparse.ArgumentParser(description="Post synthetic CSV data to the backend API.")
    parser.add_argument("--api", default=DEFAULT_API, help="Backend base URL")
    parser.add_argument("--csv-dir", default=None, help="Directory containing app_*.csv files")
    parser.add_argument("--limit-users", type=int, default=None, help="Only post N users after applying --skip-users")
    parser.add_argument("--skip-users", type=int, default=0, help="Skip the first N users from the CSV")
    parser.add_argument("--run-label", default=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
    parser.add_argument("--hr-chunk-size", type=int, default=500, help="Heart-rate samples per telemetry request")
    parser.add_argument("--ema-chunk-size", type=int, default=100, help="EMA rows per telemetry request")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be posted without sending requests")
    args = parser.parse_args()

    csv_dir = choose_csv_dir(args.csv_dir)
    base_url = args.api.rstrip("/")

    user_rows = read_csv(csv_dir / "app_user.csv")
    wearable_rows = read_csv(csv_dir / "app_wearabledevice.csv")
    hr_rows = read_csv(csv_dir / "app_heartratesample.csv")
    ema_rows = read_csv(csv_dir / "app_ema.csv")

    original_user_rows = user_rows
    user_start_index = args.skip_users
    user_end_index = None if args.limit_users is None else user_start_index + args.limit_users
    user_rows = original_user_rows[user_start_index:user_end_index]

    if args.skip_users < 0:
        raise ValueError("--skip-users must be zero or greater")

    selected_old_user_ids = {
        str(index) for index in range(user_start_index + 1, user_start_index + len(user_rows) + 1)
    }
    if wearable_rows and wearable_rows[0].get("user_id"):
        selected_old_user_ids = {
            row["user_id"]
            for row in wearable_rows[user_start_index:user_start_index + len(user_rows)]
        }

    wearable_by_old_user = {row["user_id"]: row for row in wearable_rows if row.get("user_id") in selected_old_user_ids}
    hr_by_device = defaultdict(list)
    for row in hr_rows:
        hr_by_device[row.get("device_id")].append(row)

    ema_by_old_user = defaultdict(list)
    for row in ema_rows:
        ema_by_old_user[row.get("user_id")].append(row)

    totals = {
        "users": 0,
        "heart_rate_samples": 0,
        "emas": 0,
    }

    print(f"CSV directory: {csv_dir}")
    print(f"API: {base_url}")
    print(f"Posting users: {len(user_rows)}")

    for index, user_row in enumerate(user_rows, start=1):
        original_index = user_start_index + index
        old_user_id = None
        if original_index - 1 < len(wearable_rows):
            old_user_id = wearable_rows[original_index - 1].get("user_id")
        old_user_id = old_user_id or str(original_index)

        user_payload = build_user_payload(user_row, args.run_label)
        user_response = post_json(f"{base_url}/user/", user_payload, dry_run=args.dry_run)
        new_user_id = user_response.get("user_id", f"dry-run-{old_user_id}")
        totals["users"] += 1

        wearable = wearable_by_old_user.get(old_user_id)
        wearable_payload = build_wearable_payload(wearable, args.run_label) if wearable else None
        hr_payload = build_hr_payload(hr_by_device.get(old_user_id, []))
        ema_payload = build_ema_payload(ema_by_old_user.get(old_user_id, []))

        user_hr_count = 0
        user_ema_count = 0

        if wearable_payload:
            post_json(
                f"{base_url}/telemetry/ingest/",
                {"user_id": new_user_id, "wearable_device": wearable_payload},
                dry_run=args.dry_run,
            )

        for hr_chunk in chunks(hr_payload, args.hr_chunk_size):
            telemetry_response = post_json(
                f"{base_url}/telemetry/ingest/",
                {"user_id": new_user_id, "heart_rate_samples": hr_chunk},
                dry_run=args.dry_run,
            )
            counts = telemetry_response.get("counts", {})
            user_hr_count += counts.get("heart_rate_samples", len(hr_chunk) if args.dry_run else 0)

        for ema_chunk in chunks(ema_payload, args.ema_chunk_size):
            telemetry_response = post_json(
                f"{base_url}/telemetry/ingest/",
                {"user_id": new_user_id, "emas": ema_chunk},
                dry_run=args.dry_run,
            )
            counts = telemetry_response.get("counts", {})
            user_ema_count += counts.get("emas", len(ema_chunk) if args.dry_run else 0)

        totals["heart_rate_samples"] += user_hr_count
        totals["emas"] += user_ema_count

        print(
            f"posted {index}/{len(user_rows)} (csv row {original_index}): old_user_id={old_user_id}, "
            f"new_user_id={new_user_id}, hr={user_hr_count}/{len(hr_payload)}, ema={user_ema_count}/{len(ema_payload)}"
        )

    print("DONE")
    print(json.dumps(totals, indent=2))


if __name__ == "__main__":
    main()
