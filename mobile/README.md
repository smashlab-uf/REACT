# REACT Mobile App

React Native (Expo SDK 56) client for the REACT JITAI study. Tested on physical iOS and Android devices.

## Quickstart

Needs Node 20.19.4 or newer (Expo 56 minimum). Expo Go will not work. Native `android/` and `ios/` folders are committed, and the app uses `expo-dev-client`.

```bash
cd mobile
npm install
```

The API key is not hardcoded. `src/api/config.ts` reads `process.env.EXPO_PUBLIC_API_KEY` and uses `''` if it is unset. If the server has `API_KEY` set, requests without `X-API-Key` get 403. Put the key in `mobile/.env` (gitignored):

```
EXPO_PUBLIC_API_KEY=<same value as backend API_KEY>
```

Restart Metro after changing `.env`. A reload does not pick it up.

### iOS

Needs Xcode. The first `run:ios` uses `ios/Podfile` and runs CocoaPods.

Unlock the phone, trust the computer if prompted, and enable Developer Mode (Settings → Privacy & Security). Then:

```bash
cd mobile
npx expo run:ios --device
```

That installs a debug build (`__DEV__` is true). After the native binary exists, start Metro with `npx expo start --dev-client` instead of rebuilding every time.

### Android

Needs the Android SDK and `adb`. CocoaPods is not used.

Enable USB debugging (Settings → About phone → tap Build number 7 times, then Developer options → USB debugging). Confirm the phone is visible:

```bash
adb devices
```

You want `device`, not `unauthorized`. Wireless debugging is fine too: `adb pair` / `adb connect`, then the same `adb devices` check. Then:

```bash
cd mobile
npx expo run:android --device
```

If more than one device is attached, pass the serial from `adb devices`. That also installs a debug build (`__DEV__` is true). After the native binary exists, start Metro with `npx expo start --dev-client` instead of rebuilding every time.

### After it is running

The debug Compose screen has buttons to open a check-in and to simulate pushes. Login, register, and EMA submit work against production (`ACTIVE_ENV` is `'prod'` in `src/api/config.ts`). Signing up does not start server pushes. Celery only pings users with `is_enrolled=True`, an active `WearableDevice`, and a valid Expo push token. Set enrollment and the wearable in Django Admin. `pushToken.ts` returns null unless `expo-device` reports a physical device.

To hit a local backend, set `ACTIVE_ENV` to `'dev'` in `src/api/config.ts`. Do not commit that. `dev` is `http://127.0.0.1:8000` (simulator). On a phone, point it at your computer's LAN IP and add that IP to Django `ALLOWED_HOSTS` if it is not already listed.

## Notifications

Two push types exist.

**Check-in reminder** (visible): title `REACT`, body `Time for your check-in.`, data `{ type: "checkin_reminder" }`. Celery Beat runs `send_checkin_reminders` every 180 seconds. It only sends when the participant-local hour (America/New_York) is `>= 9` and `< 21`. After a successful send it waits 120 minutes. It skips the user if today's EMA row count is already `>= 4`, or if a JITAI prompt was sent in the last 2 hours. The 9/21 hours and 120-minute gap are placeholders in `backend/app/tasks.py`.

**JITAI prompt** (silent): no title or body. Data is `{ type: "ema_prompt", prompt_id, jitai_log_id }`. Not on a clock. `evaluate_jitai_triggers` also runs every 180 seconds, but it only considers sending after a new completed EMA, then eligibility plus a coin flip. The app has no local prompt catalog, so JITAI message text is not shown on device.

Tapping either type opens EMA (`GET /ema/next/`, then `POST /ema/responses/`). A foreground check-in reminder also opens EMA. JITAI receipts (`POST /jitai/receipt/`) fire when `jitai_log_id` is present (foreground, tap, or cold start). Check-in reminders have no `jitai_log_id`, so they do not report a JITAI receipt.

## Local backend

Only needed if `ACTIVE_ENV` is `'dev'`. On a Mac:

```bash
brew install postgresql@16 redis
brew services start postgresql@16
brew services start redis
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
createdb react
```

CI and Heroku use Python 3.11 (`runtime.txt`, `.github/workflows/django-ci.yml`). Django 5.1 supports 3.10-3.13. Use 3.11 or 3.12.

```bash
cd backend
python3.12 -m venv venv   # or python3.11
source venv/bin/activate
pip install -r requirements_mac.txt
```

Create `backend/.env`:

```
SECRET_KEY=local-dev-only-not-a-real-secret
DEBUG=True
DATABASE_NAME=react
DATABASE_USER=<your macOS username>
DATABASE_HOST=localhost
DATABASE_PORT=5432
REDIS_URL=redis://localhost:6379/0
API_KEY=local-dev-key
JITAI_RANDOMIZATION_PROBABILITY=0.5
```

Match `API_KEY` to `EXPO_PUBLIC_API_KEY`. If `API_KEY` is empty, the middleware does not require the header. Prefer `DATABASE_*` over `DATABASE_URL` locally. `settings.py` sets `ssl_require=True` for any `postgres://` or `postgresql://` URL, and local Postgres usually has no SSL.

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py test
python manage.py runserver
```

Admin is http://localhost:8000/admin.

Server pushes come from Celery, not `runserver`. From `backend/` with the venv active and Redis up:

```bash
celery -A project worker --loglevel=info --concurrency=1
celery -A project beat --loglevel=info
```

## Layout

```
index.ts            Expo entry (`registerRootComponent`)
App.tsx             screen switching, push listeners, EMA modal
src/
  api/              BASE_URL, API key, axios + JWT refresh, endpoints
  store/            auth (zustand + SecureStore)
  screens/          Login, Register, Compose, EMA
  components/       ComposeInput, NotificationToast, Likert, choice, yes/no, count
  ema/              sub-item visibility
  telemetry/        compose counts, offline queue
  notifications/    push token + payload parsing
android/  ios/      committed native projects
```

No router. Screen choice is `useState` in `App.tsx`.

Compose message text is not sent. Draft telemetry sends `keystroke_count`, `delete_count`, and `time_on_compose` (plus session metadata). JITAI bodies are not stored in the database, only `prompt_id`. Check-in reminders send visible title/body because they are a scheduling nudge, not intervention copy.

## Checks

From `mobile/`:

```bash
npx tsc --noEmit
npx expo export --platform ios --output-dir /tmp/x
```

CI (`.github/workflows/django-ci.yml`) runs Django tests only. It does not build the mobile app.

## Known issues

- Push token registration skips simulators (`Device.isDevice`).
- No local JITAI prompt catalog, so silent pushes have no on-device message text.
- Register/login does not set `is_enrolled` or create a wearable.
- Check-in reminder window and 120-minute cooldown are placeholders.

`../Resources/REACT_Mobile_App_Gaps.docx` is older than this branch. EMA UI, auth refresh, and tap receipts are already implemented.
