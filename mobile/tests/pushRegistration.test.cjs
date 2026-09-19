const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

// Exercise the actual TypeScript state machine without loading native Expo modules.
const source = fs.readFileSync(require.resolve('../src/notifications/registration.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const moduleExports = {};
vm.runInNewContext(compiled.outputText, { exports: moduleExports });
const { createPushRegistration } = moduleExports;

function fixture() {
  const state = { saved: null, remote: null, offline: false, register: null };
  const dependencies = {
    read: async () => state.saved,
    write: async value => { state.saved = value; },
    register: async (userId, token) => {
      state.remote = { userId, token };
      if (state.register) await state.register();
    },
    unregister: async (userId, token) => {
      if (state.offline) throw new Error('offline');
      if (state.remote?.userId === userId && state.remote?.token === token) state.remote = null;
    },
  };
  return { state, dependencies, manager: createPushRegistration(dependencies) };
}

test('logout removes registration; login restores it', async () => {
  const { state, manager } = fixture();
  await manager.register(1, 'device', () => true);
  await manager.unregister();
  assert.equal(state.remote, null);
  assert.equal(state.saved, null);
  await manager.register(1, 'device', () => true);
  assert.equal(state.remote.token, 'device');
});

test('offline cleanup survives restart and retries without login credentials', async () => {
  const { state, manager, dependencies } = fixture();
  await manager.register(1, 'device', () => true);
  state.offline = true;
  await assert.rejects(manager.unregister());
  assert.equal(state.saved.pending, true);
  state.offline = false;
  await createPushRegistration(dependencies).retry();
  assert.equal(state.remote, null);
  assert.equal(state.saved, null);
});

test('logout during an in-flight registration cannot leave the token registered', async () => {
  const { state, manager } = fixture();
  let release;
  let started;
  let current = true;
  const ready = new Promise(resolve => { started = resolve; });
  state.register = () => new Promise(resolve => { release = resolve; started(); });
  const registration = manager.register(1, 'device', () => current);
  await ready;
  current = false;
  const logout = manager.unregister();
  release();
  await Promise.all([registration, logout]);
  assert.equal(state.remote, null);
});

test('permission/token lookup completing after logout cannot register', async () => {
  const { state, manager } = fixture();
  await manager.register(1, 'device', () => false);
  assert.equal(state.remote, null);
});

test('a lost registration response still leaves enough information to revoke', async () => {
  const { state, manager } = fixture();
  state.register = async () => { throw new Error('response lost'); };
  await assert.rejects(manager.register(1, 'device', () => true));
  assert.equal(state.saved.pending, true);
  await manager.unregister();
  assert.equal(state.remote, null);
});

test('pending cleanup completes before account switching and does not revoke new login', async () => {
  const { state, manager } = fixture();
  await manager.register(1, 'device', () => true);
  state.offline = true;
  await assert.rejects(manager.unregister());
  await assert.rejects(manager.register(2, 'device', () => true));
  assert.equal(state.remote.userId, 1);
  state.offline = false;
  await manager.register(2, 'device', () => true);
  await manager.retry();
  assert.equal(state.remote.userId, 2);
  assert.equal(state.saved.pending, false);
});
