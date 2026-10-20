const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(require.resolve('../src/ema/useAvailableCheckin.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const card = { title: 'Support resources', message: 'Exact server wording.', resources: [{ name: 'Resource', contact: '123' }] };
const inactive = { should_show: false, reason: 'no_active_check_in', distress_active: false, resource_card: null };
const active = (expiresAt = null) => ({ ...inactive, distress_active: true, resource_card: card, distress_expires_at: expiresAt });

// Exercise the actual hook with controlled lifecycles, API responses and clock.
// This never loads native modules or contacts the participant backend.
function fixture({ data = inactive, surveyOpen = false, userId = 1 } = {}) {
  let now = 1_800_000_000_000;
  let dirty = false;
  let cursor = 0;
  let result;
  const slots = [];
  let effects = [];
  let nextTimer = 0;
  const timers = new Map();
  const listeners = {};
  const props = { userId, surveyOpen };
  const api = { respond: async () => data, calls: 0 };
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const react = {
    useState(initial) {
      const i = cursor++;
      if (!slots[i]) slots[i] = { value: initial };
      return [slots[i].value, value => {
        const next = typeof value === 'function' ? value(slots[i].value) : value;
        if (!Object.is(next, slots[i].value)) { slots[i].value = next; dirty = true; }
      }];
    },
    useRef(value) {
      const i = cursor++;
      if (!slots[i]) slots[i] = { current: value };
      return slots[i];
    },
    useCallback(callback, deps) {
      const i = cursor++;
      if (!slots[i] || !same(slots[i].deps, deps)) slots[i] = { value: callback, deps };
      return slots[i].value;
    },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!slots[i] || !same(slots[i].deps, deps)) effects.push(() => {
        slots[i]?.cleanup?.();
        slots[i] = { deps, cleanup: effect() };
      });
    },
  };
  const appState = {
    currentState: 'active',
    addEventListener: (_, callback) => { listeners.app = callback; return { remove: () => { delete listeners.app; } }; },
  };
  const mocks = {
    react,
    'react-native': { AppState: appState },
    '@react-native-community/netinfo': { __esModule: true, default: { addEventListener: callback => {
      listeners.network = callback; return () => { delete listeners.network; };
    } } },
    'expo-notifications': { addNotificationReceivedListener: callback => {
      listeners.push = callback; return { remove: () => { delete listeners.push; } };
    } },
    '../api/endpoints': { ema: { next: async checkinOnly => {
      assert.equal(checkinOnly, true);
      ++api.calls;
      return { data: await api.respond() };
    } } },
    '../notifications/payload': { parsePushData: value => value, shouldOpenEMA: () => true },
  };
  function timer(callback, ms, interval = false) {
    const id = ++nextTimer;
    timers.set(id, { callback, at: now + ms, repeat: interval ? ms : null });
    return id;
  }
  const exports = {};
  vm.runInNewContext(compiled.outputText, {
    exports,
    require: name => { assert.ok(name in mocks, `Unexpected dependency: ${name}`); return mocks[name]; },
    Date: class extends Date { static now() { return now; } },
    setTimeout: (fn, ms) => timer(fn, ms), clearTimeout: id => timers.delete(id),
    setInterval: (fn, ms) => timer(fn, ms, true), clearInterval: id => timers.delete(id),
  });
  function render() {
    dirty = false;
    cursor = 0;
    result = exports.useAvailableCheckin(props.userId, props.surveyOpen);
    const pending = effects;
    effects = [];
    pending.forEach(effect => effect());
  }
  async function settle() {
    for (let i = 0; i < 12; ++i) { await Promise.resolve(); if (dirty) render(); }
  }
  render();
  return {
    api, props, settle,
    get result() { return result; },
    expires: ms => new Date(now + ms).toISOString(),
    async rerender(patch) { Object.assign(props, patch); render(); await settle(); },
    async app(state) { appState.currentState = state; listeners.app?.(state); await settle(); },
    async reconnect() { listeners.network?.({ isConnected: true }); await settle(); },
    async advance(ms) {
      const end = now + ms;
      while (true) {
        const due = [...timers].filter(([, entry]) => entry.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        const [id, entry] = due;
        now = entry.at;
        if (entry.repeat) entry.at += entry.repeat; else timers.delete(id);
        entry.callback();
        await settle();
      }
      now = end;
      await settle();
    },
    unmount() { slots.forEach(slot => slot?.cleanup?.()); },
  };
}

test('launch and relaunch restore server resources even without an available check-in', async () => {
  for (let i = 0; i < 2; ++i) {
    const f = fixture({ data: active() });
    await f.settle();
    assert.equal(f.result.resourceCard, card);
    assert.equal(f.result.available, null);
    f.unmount();
  }
});

test('submission seeds Home immediately and Done/network failure do not erase resources', async () => {
  const f = fixture({ surveyOpen: true });
  await f.settle();
  f.result.onSubmitted({ ...active(f.expires(24 * 60 * 60_000)), user: 1 });
  await f.settle();
  assert.equal(f.result.resourceCard, card);
  f.api.respond = async () => { throw new Error('offline'); };
  await f.rerender({ surveyOpen: false });
  assert.equal(f.result.resourceCard, card);
  assert.equal(f.result.error, true);
  f.unmount();
});

test('resources coexist with a check-in and clear when staff clear the baseline flag', async () => {
  const f = fixture();
  const checkin = { ...active(), should_show: true, ema_type: 'scheduled_check_in', expires_at: f.expires(30 * 60_000) };
  f.api.respond = async () => checkin;
  await f.settle();
  await f.reconnect();
  assert.equal(f.result.resourceCard, card);
  assert.equal(f.result.available, checkin);
  f.api.respond = async () => ({ ...checkin, distress_active: false, resource_card: null });
  await f.advance(60_000);
  assert.equal(f.result.resourceCard, null);
  assert.equal(f.result.available.should_show, true);
  f.unmount();
});

test('momentary card disappears at server expiry even if offline', async () => {
  const f = fixture({ surveyOpen: true });
  await f.settle();
  f.result.onSubmitted({ ...active(f.expires(10_000)), user: 1 });
  await f.settle();
  await f.advance(9_999);
  assert.equal(f.result.resourceCard, card);
  await f.advance(1);
  assert.equal(f.result.resourceCard, null);
  f.unmount();
});

test('a repeated flag extends the card past its original expiry', async () => {
  const f = fixture({ surveyOpen: true });
  await f.settle();
  f.result.onSubmitted({ ...active(f.expires(10_000)), user: 1 });
  await f.settle();
  await f.advance(5_000);
  f.result.onSubmitted({ ...active(f.expires(20_000)), user: 1 });
  await f.settle();
  await f.advance(5_000);
  assert.equal(f.result.resourceCard, card);
  await f.advance(15_000);
  assert.equal(f.result.resourceCard, null);
  f.unmount();
});

test('a delayed pre-submission response cannot erase the newly raised flag', async () => {
  const f = fixture();
  await f.settle();
  let release;
  f.api.respond = () => new Promise(resolve => { release = resolve; });
  const refreshing = f.reconnect();
  f.result.onSubmitted({ ...active(), user: 1 });
  release(inactive);
  await refreshing;
  assert.equal(f.result.resourceCard, card);
  f.unmount();
});

test('foreground return refreshes resources and background polling makes no requests', async () => {
  const f = fixture({ data: active() });
  await f.settle();
  await f.app('background');
  const calls = f.api.calls;
  f.api.respond = async () => inactive;
  await f.advance(60_000);
  assert.equal(f.api.calls, calls);
  await f.app('active');
  assert.equal(f.result.resourceCard, null);
  f.unmount();
});

test('account switching and logout hide resources and reject late submissions from the old account', async () => {
  const f = fixture({ data: active() });
  await f.settle();
  const lateSubmission = f.result.onSubmitted;
  await f.rerender({ userId: null });
  assert.equal(f.result.resourceCard, null);
  f.api.respond = async () => inactive;
  await f.rerender({ userId: 2 });
  lateSubmission({ ...active(), user: 1 });
  await f.settle();
  assert.equal(f.result.resourceCard, null);
  f.unmount();
});
