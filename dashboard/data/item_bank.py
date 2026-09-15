"""Access to the frozen EMA item bank.

Completeness is scored as answered-over-askable, and "askable" has to be
reconstructed because the served sub-item set is never persisted on the EMA row.
Reconstructing it against the live EMA_ITEM_BANK would mean that editing an item
mid-study silently rewrites every past completeness figure. So the bank is frozen
to a committed JSON file, ITEM_BANK_VERSION names which one, and every
MetricsDaily row records the version it was scored against.

Cut a new version with: python manage.py dump_item_bank --version v2
"""

import json
from functools import lru_cache
from pathlib import Path

import app

from dashboard.data.config import ITEM_BANK_VERSION

ITEM_BANK_DIR = Path(app.__file__).resolve().parent / 'data'


def item_bank_path(version=ITEM_BANK_VERSION):
    return ITEM_BANK_DIR / f'item_bank_{version}.json'


def serialize_item_bank(item_bank, version=ITEM_BANK_VERSION):
    """Deterministic payload: no timestamps, so --check can compare exactly."""
    return {'version': version, 'items': item_bank}


@lru_cache(maxsize=None)
def load_item_bank(version=ITEM_BANK_VERSION):
    path = item_bank_path(version)
    with path.open(encoding='utf-8') as f:
        return json.load(f)['items']


@lru_cache(maxsize=None)
def sub_item_index(version=ITEM_BANK_VERSION):
    """{sub_item_id: {'item_id': ..., **sub_item}} over the frozen bank.

    Mirrors app.ema_catalog.EMA_SUB_ITEM_INDEX, but built from the frozen file
    rather than the live dict.
    """
    return {
        sub_item['sub_item_id']: {'item_id': item_id, **sub_item}
        for item_id, item in load_item_bank(version).items()
        for sub_item in item['sub_items']
    }
