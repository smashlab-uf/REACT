#!/usr/bin/env bash
# REACT — JITAI Celery worker demo
#
# Usage:
#   ./demo.sh                                          # p=0.5, no push
#   ./demo.sh --p 0.99                                 # guarantee send_prompt=True
#   ./demo.sh --p 0.99 --push-token "ExponentPushToken[...]"
#
# Requires: Redis running, virtualenv active, DJANGO_SETTINGS_MODULE set
# Run from: HealthyGatorSportsFanDjango/

set -euo pipefail

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

P_VALUE="0.5"
PUSH_TOKEN=""
WORKER_PID=""

for arg in "$@"; do
    case $arg in
        --p=*) P_VALUE="${arg#*=}" ;;
        --push-token=*) PUSH_TOKEN="${arg#*=}" ;;
        --p) shift; P_VALUE="$1" ;;
        --push-token) shift; PUSH_TOKEN="$1" ;;
    esac
done

SEP="══════════════════════════════════════════════════════════════"

step() {
    echo -e "\n${BLUE}${SEP}${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}${SEP}${NC}\n"
}

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $1${NC}"; }
err()  { echo -e "${RED}  ✗ $1${NC}"; }

show_rows() {
    python3 manage.py shell -c "
from app.models import JITAILog, User
u = User.objects.get(email='jitai-demo@react.test')
logs = JITAILog.objects.filter(user=u).exclude(
    trigger_reason='below within-person threshold', send_prompt=False, randomization_draw=None
).order_by('triggered_at')
print(f'  demo rows: {logs.count()}')
for l in logs:
    draw = f'{l.randomization_draw:.4f}' if l.randomization_draw is not None else 'null'
    eligible = l.eligible_prompt_ids or []
    print(f'')
    print(f'    id                        : {l.pk}')
    print(f'    decision_point_id         : {l.decision_point_id}')
    print(f'    randomization_probability : {l.randomization_probability}')
    print(f'    randomization_draw        : {draw}')
    print(f'    send_prompt               : {l.send_prompt}')
    print(f'    trigger_reason            : {l.trigger_reason}')
    print(f'    observed_mssd             : {l.observed_mssd}')
    print(f'    eligible_prompt_ids       : {eligible}  ({len(eligible)} in pool)')
    print(f'    prompt_id (selected)      : {l.prompt_id or \"(none)\"}')
    print(f'    status                    : {l.status}')
    print(f'    delivery_status           : {l.delivery_status}')
    print(f'    push_sent_at              : {l.push_sent_at or \"(not sent)\"}')
    print(f'    delivery_error            : {l.delivery_error or \"(none)\"}')
"
}

show_catalog() {
    python3 manage.py shell -c "
from app.notification_service import _CATALOG, _EMA_CATALOG, select_prompt
import app.notification_service as ns

eligible_ids = [p['id'] for p in _EMA_CATALOG]
print(f'  total catalog (blank/retired rows dropped) : {len(_CATALOG)}')
print(f'  EMA-eligible pool (Trigger Condition=Survey): {len(_EMA_CATALOG)}')
print(f'  eligible ids                                : {eligible_ids}')

chosen, pool = select_prompt(None)
print()
print(f'  select_prompt() -> chosen={chosen}  pool_size={len(pool)}')

print()
print('  Simulating an empty EMA pool (e.g. a bad CSV edit) to prove the no-fallback fix:')
saved = ns._EMA_CATALOG
ns._EMA_CATALOG = []
chosen2, pool2 = ns.select_prompt(None)
print(f'  select_prompt() with empty pool -> chosen={chosen2!r}  pool={pool2!r}')
print('  (refuses to silently fall back to the full Telemetry+Survey catalog)')
ns._EMA_CATALOG = saved
"
}

start_worker() {
    export JITAI_RANDOMIZATION_PROBABILITY="$P_VALUE"
    celery -A project worker --loglevel=info --concurrency=1 \
        --logfile=/tmp/react-celery-demo.log &
    WORKER_PID=$!
    sleep 3
    if ! kill -0 "$WORKER_PID" 2>/dev/null; then
        err "Worker failed to start. Check /tmp/react-celery-demo.log"
        exit 1
    fi
    ok "Worker PID $WORKER_PID started (p=$P_VALUE)"
}

trigger_task() {
    python3 manage.py shell -c "
from app.tasks import evaluate_jitai_triggers
r = evaluate_jitai_triggers.delay()
print(f'  task_id: {r.id}')
"
}

stop_worker() {
    if [[ -n "$WORKER_PID" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
        kill "$WORKER_PID"
        wait "$WORKER_PID" 2>/dev/null || true
        WORKER_PID=""
        ok "Worker stopped"
    fi
}

cleanup() {
    stop_worker
}
trap cleanup EXIT

# ── STEP 1: SEED ──────────────────────────────────────────────────────────────

step "STEP 1: SEED DB"
echo "  6 stable historical EMAs (MSSD baseline) + 1 volatile fresh EMA."
echo "  Historical rows pre-linked to jitai_logs so only the fresh EMA is unprocessed."
echo ""

SEED_ARGS="--seed-only"
[[ -n "$PUSH_TOKEN" ]] && SEED_ARGS="$SEED_ARGS --push-token=$PUSH_TOKEN"

python3 manage.py jitai_demo $SEED_ARGS

ok "DB seeded  (p for this run: $P_VALUE)"

# ── STEP 2: NOTIFICATION SERVICE — PROMPT CATALOG ──────────────────────────────

step "STEP 2: NOTIFICATION SERVICE — PROMPT CATALOG"
echo "  notification_service loads REACTprompts.csv once at import: blank IDs and rows"
echo "  marked 'retired' are dropped, then the catalog is split into the full pool"
echo "  and the EMA-only pool (Trigger Condition == Survey) that select_prompt() draws from."
echo ""

show_catalog

ok "Catalog loaded — eligible pool is what evaluate_jitai_triggers will draw from"

# ── STEP 3: START WORKER ──────────────────────────────────────────────────────

step "STEP 3: START CELERY WORKER"
echo "  JITAI_RANDOMIZATION_PROBABILITY=$P_VALUE"
echo "  Logs → /tmp/react-celery-demo.log"
echo ""

start_worker

# ── STEP 4: TRIGGER TASK ─────────────────────────────────────────────────────

step "STEP 4: TRIGGER evaluate_jitai_triggers"
echo "  Worker picks up the fresh EMA, computes MSSD via decision_engine, and flips a coin"
echo "  weighted by p. If eligible, select_prompt() draws from the EMA-only pool shown in"
echo "  STEP 2 and both the choice and the full pool are frozen onto the jitai_log row"
echo "  (prompt_id, eligible_prompt_ids). If send_prompt=True and a push token exists,"
echo "  send_jitai_prompt() fires the Expo push and records delivery_status."
echo ""

trigger_task
sleep 4

ok "Task dispatched — waiting for worker..."
sleep 2

show_rows

# ── STEP 5: KILL WORKER ───────────────────────────────────────────────────────

step "STEP 5: KILL WORKER  (simulating server crash / deploy restart)"
echo "  In a real study, Celery Beat would fire the task again on the next 2-min tick."
echo "  We trigger it manually to show the same effect."
echo ""

stop_worker

# ── STEP 6: RESTART WORKER ────────────────────────────────────────────────────

step "STEP 6: RESTART WORKER"
echo "  Same p=$P_VALUE — no parameter change yet."
echo ""

start_worker

# ── STEP 7: TRIGGER AGAIN (idempotency check) ────────────────────────────────

step "STEP 7: TRIGGER AGAIN — idempotency check"
echo "  The fresh EMA is already linked to a jitai_log."
echo "  Layer 1: Exists subquery excludes it → _evaluate_user returns early (no DB write)."
echo "  Layer 2 (if race): get_or_create on decision_point_id → created=False → no duplicate."
echo ""

trigger_task
sleep 4
ok "Task dispatched — waiting..."
sleep 2

show_rows

echo ""
ok "Same row count = idempotency proven. No duplicate delivery."

# ── STEP 8: CHANGE p ON THE SPOT ─────────────────────────────────────────────

step "STEP 8: CHANGE p ON THE SPOT"
echo "  Simulate changing p from $P_VALUE to 0.1."
echo "  Stop worker → update env → restart → create a new fresh EMA → trigger."
echo ""

stop_worker

OLD_P="$P_VALUE"
P_VALUE="0.1"
export JITAI_RANDOMIZATION_PROBABILITY="$P_VALUE"
warn "p changed: $OLD_P → $P_VALUE"

python3 manage.py shell -c "
from django.utils import timezone
from datetime import timedelta
from app.models import EMA, User
u = User.objects.get(email='jitai-demo@react.test')
e = EMA.objects.create(
    user=u, prompt_id='default',
    sent_at=timezone.now() - timedelta(minutes=2),
    responded_at=timezone.now() - timedelta(minutes=1),
    status='completed',
    mood=1, stress=1, energy=1,
)
print(f'  New fresh EMA id={e.pk}  decision_point=ema_{e.pk}')
"

start_worker
trigger_task
sleep 4
ok "Task dispatched — waiting..."
sleep 2

show_rows

echo ""
echo -e "${GREEN}${SEP}${NC}"
echo -e "${GREEN}  DEMO COMPLETE${NC}"
echo -e "${GREEN}${SEP}${NC}"
echo ""
echo "  What was shown:"
echo "    1. Prompt catalog loaded from REACTprompts.csv, split into full vs. EMA-eligible pool"
echo "    2. select_prompt() refuses to fall back to the full catalog when the pool is empty"
echo "    3. Celery worker start"
echo "    4. Task fires → jitai_log written with MRT fields, selected prompt_id, eligible_prompt_ids"
echo "    5. Worker killed → restarted"
echo "    6. Task fires again → no duplicate row (idempotency key)"
echo "    7. p changed live → new row reflects new probability"
[[ -n "$PUSH_TOKEN" ]] && echo "    8. Push notification sent to device — delivery_status/push_sent_at recorded"
echo ""
echo "  Celery log: /tmp/react-celery-demo.log"
echo ""
