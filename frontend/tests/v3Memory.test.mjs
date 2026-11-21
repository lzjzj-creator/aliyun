import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';

// Load the actual API and storage code offline, replacing only Vite's API_BASE import.
const storageSource = await readFile(new URL('../src/storage.js', import.meta.url), 'utf8');
const storageUrl = `data:text/javascript;base64,${Buffer.from(storageSource).toString('base64')}`;
const apiSource = (await readFile(new URL('../src/api/v3.js', import.meta.url), 'utf8'))
  .replace('import { API_BASE } from "./core";', 'const API_BASE = "";')
  .replace('"../storage"', JSON.stringify(storageUrl));
const api = await import(`data:text/javascript;base64,${Buffer.from(apiSource).toString('base64')}`);
const storage = await import(storageUrl);
const viewSource = await readFile(new URL('../src/views/MemoryView.vue', import.meta.url), 'utf8');
const viewScript = viewSource.match(/<script setup>([\s\S]*?)<\/script>/)[1]
  .replace(/^import[\s\S]*?from ["'][^"']+["'];/gm, '');

function setup() {
  const values = new Map([['zm_anon_id', 'anon-local']]);
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, ...init, json: init.body ? JSON.parse(init.body) : undefined });
    return { ok: true, status: 200, json: async () => ({}), blob: async () => new Blob(['{}']) };
  };
  return calls;
}
function view(overrides = {}) {
  const bindings = {
    ...api, listSessions: async () => [],
    ref: (value) => ({ value }), computed: (fn) => ({ get value() { return fn(); } }),
    onMounted: () => {}, ...overrides,
  };
  return new Function(...Object.keys(bindings), `${viewScript}\nreturn {boot,onRecover,onEnable,onDisable,togglePick,onBackfill,enabled,disabledMemory,status,facts,error,onboarding,recoveryCode,recoveryError,picked};`)(...Object.values(bindings));
}

test('all v3 entrypoints include the local anon identity, including blob export', async () => {
  const calls = setup();
  api.setOwnerToken('owner');
  storage.setSessionToken('s1', 'session-secret');
  for (const run of [
    () => api.enableMemory(), api.getMemory, api.disableMemory, api.deleteAllMemory, api.exportMemory,
    () => api.listFacts(), () => api.patchFact('f', {}), () => api.deleteFact('f'),
    () => api.extractFacts('s1'), () => api.backfill(['s1']), () => api.forgetSession('s1'),
    api.listResumes, () => api.createResume({}), () => api.getResume('r'),
    () => api.patchResume('r', {}, 1), () => api.duplicateResume('r'), () => api.deleteResume('r'),
    () => api.regenerateResume('r'), () => api.listVersions('r'), () => api.restoreVersion('r', 1),
  ]) await run();
  assert.equal(calls.length, 20);
  for (const call of calls) {
    assert.equal(call.headers['X-Anon-Id'], 'anon-local');
    assert.equal(call.headers['X-Owner-Token'], 'owner');
    assert.ok(!call.url.includes('secret'));
  }
  assert.equal(calls.find((c) => c.url.endsWith('/extract')).headers['X-Session-Token'], 'session-secret');
  assert.deepEqual(calls.find((c) => c.url.endsWith('/backfill')).json,
    { session_ids: ['s1'], session_tokens: { s1: 'session-secret' } });
});

test('unknown local session credentials reject before any network request; no implicit backfill', async () => {
  const calls = setup();
  storage.setSessionToken('known', 'token');
  for (const run of [() => api.extractFacts('unknown'), () => api.backfill(['known', 'unknown']),
    () => api.backfill(), () => api.backfill([])]) await assert.rejects(run, /凭据/);
  assert.equal(calls.length, 0);
  const page = view();
  page.togglePick('unknown');
  assert.equal(page.picked.value.size, 0);
  assert.match(page.error.value, /本地凭据/);
  assert.match(viewSource, /:disabled="backfilling \|\| !hasSessionCredential\(s.id\)"/);
});

test('recovery exchanges a body code and persists rotated token before subsequent requests', async () => {
  const calls = setup();
  api.setOwnerToken('old');
  globalThis.fetch = async (url, init) => {
    calls.push({url, ...init});
    return {ok: true, json: async () => ({owner_token:'new', recovery_code:'new-code'})};
  };
  const result = await api.recoverMemory('  old-code  ');
  assert.equal(result.recovery_code, 'new-code');
  assert.equal(api.getOwnerToken(), 'new');
  assert.deepEqual(JSON.parse(calls[0].body), {recovery_code:'old-code'});
  assert.equal(calls[0].headers['X-Anon-Id'], 'anon-local');
  assert.ok(!calls[0].url.includes('old-code'));
  await api.getMemory();
  assert.equal(calls[1].headers['X-Owner-Token'], 'new');
});

test('recovery errors preserve the previous token and provide actionable UI errors', async () => {
  for (const [status, hint] of [[404, /无效或已使用/], [429, /频繁/], [422, /格式/], [503, /稍后重试/]]) {
    setup(); api.setOwnerToken('old');
    globalThis.fetch = async () => ({ok:false, status, json: async () => ({detail:'unavailable'})});
    const page = view(); page.recoveryCode.value = 'bad-code';
    await page.onRecover();
    assert.equal(api.getOwnerToken(), 'old');
    assert.match(page.recoveryError.value, hint);
    assert.equal(page.onboarding.value, null);
  }
});

test('successful recovery displays new credentials even when status refresh fails', async () => {
  setup(); api.setOwnerToken('old');
  const page = view({
    recoverMemory: async () => { api.setOwnerToken('new'); return {owner_token:'new',recovery_code:'new-code'}; },
    getMemory: async () => { throw Object.assign(new Error('offline'), {status:503}); },
  });
  page.recoveryCode.value = 'old-code';
  await page.onRecover();
  assert.equal(page.recoveryCode.value, '');
  assert.equal(page.onboarding.value.owner_token, 'new');
  assert.equal(page.onboarding.value.recovery_code, 'new-code');
  assert.ok(viewSource.indexOf('v-if="onboarding"') < viewSource.indexOf('v-if="!booted"'));
});

test('disabled status survives refresh without calling forbidden facts and re-enables with old owner', async () => {
  setup(); api.setOwnerToken('same-owner-token');
  let active = false, factsCalls = 0, enableCalls = 0;
  const page = view({
    getMemory: async () => ({enabled:active}),
    listFacts: async () => { factsCalls++; return {facts:[],next_cursor:null}; },
    enableMemory: async () => { enableCalls++; assert.equal(api.getOwnerToken(),'same-owner-token'); active=true; return {reactivated:true}; },
  });
  await page.boot();
  assert.equal(page.enabled.value, false);
  assert.equal(page.disabledMemory.value, true);
  assert.equal(factsCalls, 0);
  await page.onEnable();
  assert.equal(page.enabled.value, true);
  assert.equal(page.disabledMemory.value, false);
  assert.equal(enableCalls, 1);
  assert.equal(factsCalls, 1);
  assert.equal(page.onboarding.value, null);
  assert.equal(api.getOwnerToken(), 'same-owner-token');
});

test('Vue SFC template and script compile offline when frontend dependencies are installed', async (t) => {
  let compiler;
  try { compiler = await import('@vue/compiler-sfc'); }
  catch (error) {
    if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
    t.skip('frontend dependencies are not installed; no network/package installation performed');
    return;
  }
  const {parse, compileScript, compileTemplate} = compiler;
  const {descriptor, errors} = parse(viewSource);
  assert.deepEqual(errors, []);
  const script = compileScript(descriptor, {id:'memory-test'});
  const compiled = compileTemplate({source:descriptor.template.content, filename:'MemoryView.vue', id:'memory-test', compilerOptions:{bindingMetadata:script.bindings}});
  assert.deepEqual(compiled.errors, []);
});
