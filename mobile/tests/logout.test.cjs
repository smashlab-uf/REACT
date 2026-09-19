const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

function loadStore(unregister) {
  const events = [];
  let onAuthFailure;
  const mocks = {
    zustand: { create: factory => {
      let state;
      const set = patch => { state = { ...state, ...patch }; };
      state = factory(set);
      return { getState: () => state };
    } },
    'expo-secure-store': {
      setItemAsync: async () => {},
      deleteItemAsync: async key => { events.push(key); },
    },
    'expo-notifications': Object.fromEntries([
      'cancelAllScheduledNotificationsAsync', 'dismissAllNotificationsAsync',
      'clearLastNotificationResponseAsync',
    ].map(name => [name, async () => { events.push(name); }])),
    '../api/client': {
      setTokens: () => {},
      clearTokens: () => { events.push('clearTokens'); },
      setOnAuthFailure: fn => { onAuthFailure = fn; },
    },
    '../api/endpoints': { auth: {} },
    '../utils/logger': { log: () => {} },
    '../notifications/session': { pushRegistration: {
      unregister: async () => { events.push('unregister'); await unregister(); },
    } },
  };
  const source = fs.readFileSync(require.resolve('../src/store/authStore.ts'), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
  const exports = {};
  vm.runInNewContext(compiled.outputText, { exports, require: name => {
    assert.ok(name in mocks, `Unexpected dependency: ${name}`);
    return mocks[name];
  } });
  return { store: exports.useAuthStore, events, failAuth: () => onAuthFailure() };
}

test('logout disables the UI immediately and revokes before clearing credentials', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const { store, events } = loadStore(() => pending);
  await store.getState().login('access', 'refresh', { user_id: 1 });
  const logout = store.getState().logout();
  assert.equal(store.getState().isAuthenticated, false);
  assert.equal(store.getState().isLoading, true);
  assert.deepEqual(events, ['unregister']);
  const secondLogout = store.getState().logout();
  release();
  await Promise.all([logout, secondLogout]);
  assert.equal(events.filter(event => event === 'unregister').length, 1);
  assert.ok(events.indexOf('auth_tokens') > events.indexOf('unregister'));
  assert.ok(events.includes('cancelAllScheduledNotificationsAsync'));
  assert.ok(events.includes('dismissAllNotificationsAsync'));
  assert.equal(store.getState().isLoading, false);
});

test('failed remote cleanup still completes local logout', async () => {
  const { store, events } = loadStore(async () => { throw new Error('offline'); });
  await store.getState().login('access', 'refresh', { user_id: 1 });
  await store.getState().logout();
  assert.equal(store.getState().isAuthenticated, false);
  assert.equal(store.getState().userId, null);
  assert.ok(events.includes('clearTokens'));
});

test('expired sessions use the same notification cleanup', async () => {
  const { store, events, failAuth } = loadStore(async () => {});
  await store.getState().login('access', 'refresh', { user_id: 1 });
  failAuth();
  await store.getState().logout();
  assert.equal(store.getState().isAuthenticated, false);
  assert.equal(events.filter(event => event === 'unregister').length, 1);
});
