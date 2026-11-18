// Offline source-function regressions. Built-ins only; no network or installs.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = (path) => readFileSync(new URL('../' + path, import.meta.url), 'utf8');
const core = 'const API_BASE = String();\n' + source('frontend/src/api/core.js')
  .split('\n').filter((line) => !line.startsWith('import ')
    && !line.startsWith('export const API_BASE')).join('\n')
  .replaceAll('export ', '');

async function checkStream(chunks, label) {
  const events = [];
  const context = vm.createContext({
    TextDecoder,
    getAnonId: () => 'anon',
    getSessionToken: () => 'token',
    fetch: async () => ({
      ok: true,
      body: { getReader: () => ({
        read: async () => chunks.length
          ? { done: false, value: chunks.shift() } : { done: true },
      }) },
    }),
  });
  vm.runInContext(core, context);
  await context.sse('/test', {
    body: {},
    handlers: Object.fromEntries(['session', 'token', 'done'].map((event) =>
      [event, (data) => events.push([event, JSON.parse(JSON.stringify(data))])])),
  });
  assert.deepEqual(events, [
    ['session', { session_id: 's', session_token: 'one-time-token' }],
    ['token', '中文内容'],
    ['done', { message_id: 1 }],
  ], label);
}

for (const newline of ['\n', '\r\n']) {
  const frames = [
    ['session', { session_id: 's', session_token: 'one-time-token' }],
    ['token', '中文内容'],
    ['done', { message_id: 1 }],
  ].map(([event, data]) => 'event: ' + event + newline + 'data: '
    + JSON.stringify(data) + newline + newline).join('');
  const bytes = new TextEncoder().encode(frames);
  for (const cut of [...bytes.keys(), bytes.length]) {
    await checkStream([bytes.slice(0, cut), bytes.slice(cut)],
      'SSE loss or duplication at byte split ' + cut);
  }
  await checkStream(Array.from(bytes, (byte) => Uint8Array.of(byte)),
    'SSE loss with every byte in its own chunk');
}

const functionSlice = (text, start, end) => {
  const from = text.indexOf(start);
  const to = text.indexOf(end, from);
  assert.notEqual(from, -1);
  assert.notEqual(to, -1);
  return text.slice(from, to);
};
const removeEntry = functionSlice(source('frontend/src/components/ResumeFieldsForm.vue'),
  'function removeEntry(', 'const join');
for (const key of ['education', 'internships', 'projects']) {
  for (const index of [1, 2]) {
    const original = { [key]: [{ text: 'first' }, { text: 'middle' }, { text: 'last' }],
      untouched: { text: 'keep' } };
    let emitted;
    const context = vm.createContext({
      props: { modelValue: original },
      emit: (event, value) => {
        assert.equal(event, 'update:modelValue');
        emitted = JSON.parse(JSON.stringify(value));
      },
    });
    vm.runInContext(removeEntry, context);
    context.removeEntry(key, index);
    assert.deepEqual(emitted[key], original[key].filter((_, i) => i !== index));
    assert.deepEqual(emitted.untouched, original.untouched);
    assert.equal(original[key].length, 3, 'must not mutate props');
  }
}

const boot = functionSlice(source('frontend/src/views/InterviewView.vue'),
  'async function boot()', 'onMounted(boot)');
let calls = 0;
let restored = 0;
const dead = { value: '' };
const retry = { value: false };
const fallback = { value: false };
const context = vm.createContext({
  sessionId: { value: 'existing-session' },
  dead, deadCanRetry: retry, deadFallbackV1: fallback,
  slotLabels: {}, getTemplate: async () => ({ interview_slots: [] }),
  getSession: async () => {
    calls += 1;
    if (calls === 1) throw Object.assign(new Error('temporary failure'), { status: 503 });
    return { messages: [] };
  },
  restoreMessages: () => { restored += 1; },
  scroll: () => {},
});
vm.runInContext(boot, context);
await context.boot();
assert.ok(dead.value);
assert.equal(retry.value, true);
await context.boot();
assert.equal(restored, 1);
assert.equal(dead.value, '');
assert.equal(retry.value, false);
assert.equal(fallback.value, false);
console.log('PASS: SSE byte boundaries, immutable entry deletion, successful load retry');
