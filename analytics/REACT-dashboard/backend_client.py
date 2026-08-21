from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd


LOCAL_TIMEZONE = "America/New_York"


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MOCK_PATH = BASE_DIR / "data" / "mock_backend_health.json"

PARTICIPANTS_URL = (
    "https://healthygatorsportfan-ab9271b02569.herokuapp.com/"
    "dashboard/participants/"
)
LATENCY_EVENTS_URL = (
    "https://healthygatorsportfan-ab9271b02569.herokuapp.com/"
    "dashboard/latency-events/?limit=500"
)


def _read_json(url: str, api_key: str) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Dashboard-API-Key": api_key,
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Backend returned HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach backend: {exc.reason}") from exc


def _load_mock() -> list[dict[str, Any]]:
    with DEFAULT_MOCK_PATH.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, list):
        raise ValueError("Mock backend file must contain a JSON list.")

    return payload


def _load_live(api_key: str) -> list[dict[str, Any]]:
    participants = _read_json(PARTICIPANTS_URL, api_key)
    latency_events = _read_json(LATENCY_EVENTS_URL, api_key)

    if not isinstance(participants, list):
        raise ValueError("Participants endpoint did not return a JSON list.")
    if not isinstance(latency_events, list):
        raise ValueError("Latency endpoint did not return a JSON list.")

    participant_map: dict[Any, dict[str, Any]] = {}
    for row in participants:
        key = row.get("participant_id") or row.get("user_id")
        participant_map[key] = dict(row)

    combined_rows: list[dict[str, Any]] = []
    event_keys = set()

    for event in latency_events:
        key = event.get("participant_id") or event.get("user_id")
        event_keys.add(key)
        combined = participant_map.get(key, {}).copy()
        combined.update(event)
        combined_rows.append(combined)

    for key, row in participant_map.items():
        if key not in event_keys:
            combined_rows.append(row)

    return combined_rows


def load_backend_health(use_mock: Optional[bool] = None) -> tuple[pd.DataFrame, str]:
    """Load either the live backend or seed/mock backend data explicitly.

    When ``use_mock`` is None, preserve the environment-variable behavior for
    backward compatibility. Passing True/False lets the dashboard's global
    Data mode switch control the source without silently mixing modes.
    """
    if use_mock is None:
        use_mock = os.getenv("REACT_USE_MOCK_DATA", "false").strip().lower() in {
            "1", "true", "yes", "on"
        }

    if use_mock:
        records = _load_mock()
        source = "seed data"
        is_seed_data = True
    else:
        api_key = os.getenv("REACT_DASHBOARD_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "REACT_DASHBOARD_API_KEY is not set in the environment."
            )
        records = _load_live(api_key)
        source = "live backend"
        is_seed_data = False

    frame = pd.DataFrame(records)
    frame["data_source"] = source
    frame["is_seed_data"] = is_seed_data

    timestamp_columns = [
        "last_sync_timestamp",
        "last_push_timestamp",
        "last_receipt_timestamp",
        "decision_made_at",
        "push_sent_timestamp",
        "receipt_timestamp",
        "receipt_reported_at",
    ]

    for column in timestamp_columns:
        if column in frame.columns:
            frame[column] = (
                pd.to_datetime(frame[column], errors="coerce", utc=True)
                .dt.tz_convert(LOCAL_TIMEZONE)
            )

    return frame, source