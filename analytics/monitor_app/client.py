"""HTTP access to the monitoring API.

The prototype deliberately holds no database credential and imports nothing from
Django. It reads the same five endpoints an RA's browser would, so anything that
renders here is something the API can actually serve.

Responses are cached for CACHE_TTL seconds. The metric tables are rebuilt by
dashboard.tasks.recompute_monitoring_metrics every 600 s, so a shorter TTL only
re-fetches rows that cannot have changed.
"""

import os

import requests
import streamlit as st

CACHE_TTL = 60
TIMEOUT = 20

BASE_URL_ENV = 'MONITOR_BASE_URL'
API_KEY_ENV = 'DASHBOARD_API_KEY'
DEFAULT_BASE_URL = 'http://localhost:8000'


class MonitorError(RuntimeError):
    pass


def base_url():
    return os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL).rstrip('/')


def _api_key():
    key = os.environ.get(API_KEY_ENV, '')
    if not key:
        raise MonitorError(
            f'{API_KEY_ENV} is not set. Export the dashboard key before starting '
            f'Streamlit; it is never read from a file or a command line here.'
        )
    return key


def _get(path, params=None):
    try:
        response = requests.get(
            f'{base_url()}{path}',
            params=params or {},
            headers={'X-Dashboard-API-Key': _api_key()},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise MonitorError(f'Could not reach {base_url()}{path}: {exc}') from exc

    if response.status_code == 403:
        raise MonitorError(
            f'{base_url()}{path} rejected the key. Check that {API_KEY_ENV} matches '
            f'the backend DASHBOARD_API_KEY.'
        )
    if not response.ok:
        raise MonitorError(f'{path} returned {response.status_code}: {response.text[:200]}')
    return response.json()


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def grid(metric, phase='all'):
    return _get('/api/monitor/grid', {'metric': metric, 'phase': phase})


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def cohort(phase='all'):
    return _get('/api/monitor/cohort', {'phase': phase})


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def alerts(severity=None):
    return _get('/api/monitor/alerts', {'severity': severity} if severity else None)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def participant(user_id):
    return _get(f'/api/monitor/participant/{user_id}')


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def timeline(user_id, local_date=None, days=1):
    params = {'days': days}
    if local_date:
        params['date'] = local_date
    return _get(f'/api/monitor/participant/{user_id}/timeline', params)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def funnel(user_id):
    return _get(f'/api/monitor/participant/{user_id}/funnel')
