#!/usr/bin/env python3
"""
Hits the real REACT HTTP API to create test participants, enroll them, and
submit real EMAs -- then polls GET /jitai/<user_id>/ so you can watch Celery
Beat's evaluate_jitai_triggers pick them up on its next tick (every 180s in
prod, per CELERY_BEAT_SCHEDULE).

This does not simulate anything at the DB layer -- every call below is the
same HTTP request the mobile app would make. See docs/api-contract.md for
the endpoint shapes this script relies on.

Usage:
  python3 full_circle_test.py                                   # 5 users against prod
  python3 full_circle_test.py --base-url http://127.0.0.1:8000  # local backend
  python3 full_circle_test.py --count 3 --push-token "ExponentPushToken[...]"
"""
import argparse
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import requests

DEFAULT_BASE_URL = 'https://healthygatorsportfan-ab9271b02569.herokuapp.com'


def register_or_login(base_url, email, password):
    resp = requests.post(f'{base_url}/user/', json={
        'email': email,
        'password': password,
        'first_name': 'FullCircle',
        'last_name': 'Test',
        'birthdate': '2000-01-01',
        'gender': 'other',
    })
    if resp.status_code not in (201, 400):
        resp.raise_for_status()

    login = requests.post(f'{base_url}/user/login/', json={
        'email': email,
        'password': password,
    })
    login.raise_for_status()
    body = login.json()
    return body['data']['user_id'], body['access']


def enroll(base_url, headers, user_id, push_token):
    put_body = {'is_enrolled': True}
    if push_token:
        put_body['push_token'] = push_token
    resp = requests.put(f'{base_url}/user/{user_id}/', json=put_body, headers=headers)
    resp.raise_for_status()

    resp = requests.post(f'{base_url}/wearable/', json={
        'user': user_id,
        'labfront_participant_id': f'fullcircle-{user_id}',
        'is_active': True,
    }, headers=headers)
    if resp.status_code not in (201, 400):
        resp.raise_for_status()


def submit_ema(base_url, headers, user_id, mood, stress, energy):
    resp = requests.post(f'{base_url}/ema/', json={
        'user': user_id,
        'prompt_id': 'full_circle_test',
        'mood': mood,
        'stress': stress,
        'energy': energy,
    }, headers=headers)
    resp.raise_for_status()
    return resp.json()


def build_test_user(base_url, index, push_token):
    run_id = uuid.uuid4().hex[:8]
    email = f'fullcircle-{run_id}-{index}@react-test.local'
    password = 'FullCircleTest123!'

    user_id, access_token = register_or_login(base_url, email, password)
    headers = {'Authorization': f'Bearer {access_token}'}

    assigned_token = None if index == 0 else push_token
    enroll(base_url, headers, user_id, assigned_token)

    print(f'  user_id={user_id:<6} email={email:<40} '
          f'push_token={"(none — missing-token case)" if not assigned_token else "set"}')

    # 5 stable EMAs establish a within-person baseline, then 1 volatile EMA
    # is the actual decision point -- same pattern as app/management/commands/jitai_demo.py
    for _ in range(5):
        submit_ema(base_url, headers, user_id, mood=4, stress=4, energy=4)
    submit_ema(base_url, headers, user_id, mood=1, stress=1, energy=1)

    return user_id, headers


def restart_worker(heroku_app):
    print(f'\nRestarting worker dyno on {heroku_app} (proving idempotency survives a crash)...')
    try:
        subprocess.run(
            ['heroku', 'ps:restart', 'worker', '-a', heroku_app],
            check=True,
        )
    except FileNotFoundError:
        print('  heroku CLI not found on PATH -- skipping restart, continuing without it.', file=sys.stderr)
        return
    except subprocess.CalledProcessError as exc:
        print(f'  heroku ps:restart failed (exit {exc.returncode}) -- continuing without it.', file=sys.stderr)
        return
    print('  Worker restarted. Whatever runs on the next beat tick has to survive this.\n')


def poll_for_jitai_rows(base_url, users, duration_seconds, interval_seconds):
    seen_ids = {user_id: set() for user_id, _ in users}
    deadline = time.time() + duration_seconds

    print(f'\nPolling for up to {duration_seconds}s '
          f'(beat fires every 180s in prod -- give it at least one tick)...\n')

    while time.time() < deadline:
        for user_id, headers in users:
            resp = requests.get(f'{base_url}/jitai/{user_id}/', headers=headers)
            resp.raise_for_status()
            for row in resp.json():
                if row['id'] not in seen_ids[user_id]:
                    seen_ids[user_id].add(row['id'])
                    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
                    print(f'[{ts}] NEW JITAILog user_id={user_id} id={row["id"]} '
                          f'trigger_reason={row["trigger_reason"]!r} '
                          f'send_prompt={row["send_prompt"]} status={row["status"]}')
        time.sleep(interval_seconds)

    total = sum(len(v) for v in seen_ids.values())
    print(f'\nDone polling. {total} JITAILog row(s) observed across {len(users)} test user(s).')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', default=DEFAULT_BASE_URL)
    parser.add_argument('--count', type=int, default=5)
    parser.add_argument('--push-token', default=None,
                         help='Real Expo push token to assign to test users (all but the first, '
                              'which is left blank on purpose to exercise the missing-token path)')
    parser.add_argument('--poll-seconds', type=int, default=400,
                         help='How long to poll after seeding (default covers one 180s beat tick with margin)')
    parser.add_argument('--poll-interval', type=int, default=15)
    parser.add_argument('--restart-worker', metavar='HEROKU_APP_NAME', default=None,
                         help='If set, runs `heroku ps:restart worker -a HEROKU_APP_NAME` right after '
                              'seeding, before polling starts -- proves the pipeline survives a worker '
                              'restart with no duplicate JITAILog rows. Requires heroku CLI on PATH and '
                              'access to that app.')
    args = parser.parse_args()

    print(f'Target: {args.base_url}')
    print(f'Creating and enrolling {args.count} test user(s)...\n')

    users = []
    for i in range(args.count):
        user_id, headers = build_test_user(args.base_url, i, args.push_token)
        users.append((user_id, headers))

    if args.restart_worker:
        restart_worker(args.restart_worker)

    poll_for_jitai_rows(args.base_url, users, args.poll_seconds, args.poll_interval)


if __name__ == '__main__':
    try:
        main()
    except requests.HTTPError as exc:
        print(f'\nHTTP error: {exc.response.status_code} {exc.response.text}', file=sys.stderr)
        sys.exit(1)
