import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import * as Vue from 'vue';
import { ref, computed, watch, nextTick, effectScope, reactive, onScopeDispose } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';

const read = (path) => readFile(new URL(path, import.meta.url), 'utf8');
const sources = {
  library: await read('../src/views/ResumeLibraryView.vue'),
  wizard: await read('../src/views/ResumeWizardView.vue'),
  v3: await read('../src/api/v3.js'),
  resume: await read('../src/api/resume.js'),
};
// Execute real scripts with only I/O and component imports isolated. Vue's actual
// refs/computed/watch scheduler is used so a fake synchronous watch cannot hide dirty bugs.
function evaluate(source, bindings, names) {
  const script = (source.match(/<script setup>([\s\S]*?)<\/script>/)?.[1] || source)
    .replace(/^import[\s\S]*?from ["'][^"']+["'];/gm, '')
    .replace(/import\.meta\.env\.DEV/g, 'false')
    .replace(/^export /gm, '');
  return new Function(...Object.keys(bindings), `${script}\nreturn {${names.map((n) => `${n}: typeof ${n} === 'undefined' ? undefined : ${n}`).join(',')}};`)(...Object.values(bindings));
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((ok, no) => { resolve = ok; reject = no; });
  return { promise, resolve, reject };
}
const initial = () => ({ id: 'doc-1', title: '我的简历', version: 3, template_version: 't1', fields: { basic: { name: '小林' } }, gaps: [] });
function page(t, kind, overrides = {}) {
  const scope = effectScope();
  t.after(() => scope.stop());
  const calls = [];
  const props = reactive({docId: 'doc-1'});
  const bindings = {
    ref, computed, watch, onMounted: () => {}, onBeforeUnmount: onScopeDispose,
    defineProps: () => props, defineEmits: () => (...args) => calls.push(['emit', ...args]),
    listResumes: async () => ({documents: []}),
    getOwnerToken: () => 'owner', getCurrentSessionId: () => 'selected-session',
    getTemplate: async () => ({template_version: 't1', sections: []}),
    getResume: async () => initial(), listVersions: async () => ({versions: []}),
    patchResume: async (id, body, version) => {
      calls.push(['save', id, JSON.parse(JSON.stringify(body)), version]);
      return {document: {...initial(), fields: body.fields, version: version + 1}};
    },
    exportResumePdf: async (...args) => { calls.push(['pdf', ...args]); return new Blob(['pdf'], {type:'application/pdf'}); },
    exportDraftPdf: async (...args) => { calls.push(['draft-pdf', ...args]); return new Blob(['pdf'], {type:'application/pdf'}); },
    downloadResumePdf: (...args) => calls.push(['download', ...args]),
    exportIssues: () => [],
    DRAFT_KEY: 'draft', OPT_DRAFT_KEY: 'opt', saveDraft: () => {}, loadDraft: () => null,
    confirm: () => true, window: {confirm: () => true}, document: {querySelector: () => ({})},
    downloadElementAsPdf: () => calls.push(['legacy-screenshot']),
    ...overrides,
  };
  const names = kind === 'library'
    ? ['doc','fields','dirty','busy','saving','exporting','optBusy','optFields','tpl','layout','errMsg','exportProblems','saveState','loadDoc','onSave','onExport','adoptOptimized','onRegenerate','onRestore','onDuplicate','onOptimize']
    : ['fields','tpl','draftVer','step','optFields','busy','optBusy','exporting','layout','errMsg','exportProblems','saveState','displaySaveState','exportPrint','saveLocalDraft','adoptAll'];
  names.push('discardOptimized', 'evidenceRows', 'evidence', 'optPlan', 'optResults', 'plan', 'results', 'versions', 'loading', 'doOptimize', 'photoBusy', 'photoContext', 'onPhotoBusy', 'doAutofill', 'loadTemplate', 'formDisabled', 'locked', 'keepManual', 'mobilePanel', 'onFieldsUpdate');
  const state = scope.run(() => evaluate(sources[kind], bindings, names));
  return { ...state, calls, props, unmount: () => scope.stop() };
}
async function library(t, overrides) {
  const p = page(t, 'library', overrides);
  await p.loadDoc('doc-1');
  p.tpl.value = {template_version: 't1', sections: []};
  await nextTick();
  return p;
}
function wizard(t, overrides) {
  const p = page(t, 'wizard', overrides);
  p.tpl.value = {template_version:'t1', sections:[]};
  p.draftVer.value = 't1';
  p.fields.value = initial().fields;
  p.step.value = 2;
  return p;
}

test('library load and nested edits use a detached saved baseline, without nextTick races', async (t) => {
  const p = await library(t);
  assert.equal(p.dirty.value, false);
  p.fields.value.basic.name = '新名字';
  assert.equal(p.doc.value.fields.basic.name, '小林');
  assert.equal(p.dirty.value, true);
  await p.onSave();
  await nextTick();
  assert.equal(p.dirty.value, false);
  assert.equal(p.doc.value.version, 4);
});

test('library exports only after saving and uses the returned version and selected layout', async (t) => {
  const p = await library(t);
  p.fields.value = {basic:{name:'已修改'}}; // deliberately no nextTick before export
  if (p.layout) p.layout.value = 'modern';
  await p.onExport();
  assert.deepEqual(p.calls.map((c) => c[0]), ['save','pdf','download']);
  assert.deepEqual(p.calls[1], ['pdf','doc-1',{version:4,layout:'modern'}]);
  await nextTick();
  assert.equal(p.dirty.value, false);
});

test('library PATCH failure/conflict stops PDF and keeps unsaved edits', async (t) => {
  const p = await library(t, {patchResume: async () => { throw Object.assign(new Error('offline'), {status:409}); }});
  p.fields.value = {basic:{name:'未保存'}};
  await p.onExport();
  assert.equal(p.calls.length, 0);
  assert.equal(p.dirty.value, true);
  assert.match(p.errMsg.value, /版本冲突/);
  assert.equal(p.busy.value, false);
});

test('library rejects unadopted optimization rather than exporting an older draft', async (t) => {
  const p = await library(t);
  p.optFields.value = {basic:{name:'优化稿'}};
  await p.onExport();
  assert.equal(p.calls.length, 0);
  assert.match(p.errMsg.value, /采纳/);
  p.adoptOptimized();
  await p.onExport();
  assert.deepEqual(p.calls.map((c) => c[0]), ['save','pdf','download']);
});

test('unadopted optimization is not reported saved; adoption clears the obsolete export warning', async (t) => {
  const p = await library(t);
  p.optFields.value = {basic:{name:'优化稿'}};
  assert.match(p.saveState.value, /待采纳/);
  await p.onExport();
  p.adoptOptimized();
  assert.equal(p.errMsg.value, '');
  assert.equal(p.dirty.value, true);
  const w = wizard(t);
  w.step.value = 3;
  w.saveState.value = '已保存到本机';
  w.optFields.value = {basic:{name:'优化稿'}};
  assert.match(w.displaySaveState.value, /待采纳|未保存/);
  w.step.value = 2;
  assert.equal(w.displaySaveState.value, '已保存到本机');
});

test('library prevents concurrent save/export and does not erase edits made during PATCH', async (t) => {
  const wait = deferred();
  let saves = 0;
  const p = await library(t, {patchResume: async () => { saves++; return wait.promise; }});
  p.fields.value = {basic:{name:'保存快照'}};
  const exporting = p.onExport();
  const secondExport = p.onExport();
  const secondSave = p.onSave();
  p.fields.value.basic.name = '请求期间新修改';
  wait.resolve({document:{...initial(), version:4, fields:{basic:{name:'保存快照'}}}});
  await Promise.all([exporting, secondExport, secondSave]);
  await nextTick();
  assert.equal(saves, 1);
  assert.equal(p.fields.value.basic.name, '请求期间新修改');
  assert.equal(p.dirty.value, true);
  assert.equal(p.calls.length, 0);
  assert.match(p.errMsg.value, /未保存|修改/);
});

test('library prevents export while a manual save or optimization is pending', async (t) => {
  const wait = deferred();
  const p = await library(t, {patchResume: () => wait.promise});
  p.fields.value = {basic:{name:'新'}};
  const save = p.onSave();
  await p.onExport();
  assert.equal(p.calls.length, 0);
  wait.resolve({document:{...initial(), version:4, fields:{basic:{name:'新'}}}});
  await save;
  p.optBusy.value = true;
  await p.onExport();
  assert.equal(p.calls.length, 0);
});

test('both pages block invalid fields and list actionable renderer issues, not confirm-and-export', async (t) => {
  const issues = [{field_path:'basic.name',label:'姓名',reason:'必填'}];
  for (const kind of ['library','wizard']) {
    const p = kind === 'library' ? await library(t, {exportIssues: () => issues}) : wizard(t, {exportIssues: () => issues});
    await (p.onExport || p.exportPrint)();
    assert.equal(p.calls.length, 0);
    assert.deepEqual(p.exportProblems.value, issues);
    assert.match(p.errMsg.value, /补全|修正/);
  }
});

test('wizard exports an explicit current snapshot without owner memory or implicit session', async (t) => {
  const p = wizard(t);
  if (p.layout) p.layout.value = 'modern';
  await p.exportPrint();
  assert.deepEqual(p.calls.map((c) => c[0]), ['draft-pdf','download']);
  assert.deepEqual(p.calls[0][1], {template_version:'t1',fields:initial().fields,layout:'modern'});
  p.fields.value.basic.name = 'later';
  assert.equal(p.calls[0][1].fields.basic.name, '小林');
});

test('wizard exports the visible optimized snapshot and suppresses double-clicks', async (t) => {
  const wait = deferred();
  const seen = [];
  const p = wizard(t, {exportDraftPdf: async (body) => { seen.push(body); return wait.promise; }});
  p.step.value = 3;
  p.optFields.value = {basic:{name:'优化后'}};
  const first = p.exportPrint();
  await p.exportPrint();
  assert.equal(seen.length, 1);
  assert.equal(seen[0].fields.basic.name, '优化后');
  wait.resolve(new Blob(['pdf'], {type:'application/pdf'}));
  await first;
  assert.equal(p.exporting.value, false);
});

test('server PDF failures leave both pages retryable and expose detail issues', async (t) => {
  const issues = [{field_path:'basic.name',label:'姓名',reason:'请填写'}];
  const fail = async () => { throw Object.assign(new Error('版本已变更'), {status:409, issues}); };
  for (const kind of ['library','wizard']) {
    const p = kind === 'library' ? await library(t, {exportResumePdf: fail}) : wizard(t, {exportDraftPdf: fail});
    await (p.onExport || p.exportPrint)();
    assert.match(p.errMsg.value, /版本已变更/);
    assert.deepEqual(p.exportProblems.value, issues);
    assert.equal(p.exporting.value, false);
    assert.ok(!p.calls.some((c) => c[0] === 'download'));
  }
});

test('wizard only reports local saved after read-back confirms the draft', async (t) => {
  let stored = null;
  const p = wizard(t, {saveDraft: (_key, body) => { stored = JSON.parse(JSON.stringify(body)); }, loadDraft: () => stored});
  await nextTick();
  assert.equal(p.saveState.value, '已保存到本机');
  const failed = wizard(t);
  await nextTick();
  assert.match(failed.saveState.value, /失败/);
});

const documentFor = (id, name = id) => ({...initial(), id, title: id, fields: {basic: {name}}});
const flushRequests = async () => { for (let i = 0; i < 8; i++) await nextTick(); };
const optimizationPlan = () => ({sections: [
  {key: 'basic', optimizable: true, status: 'pending'},
  {key: 'summary', optimizable: true, status: 'pending'},
]});

test('library ignores slow A details after B arrives and clears A immediately on navigation', async (t) => {
  const a = deferred();
  const p = page(t, 'library', {getResume: (id) => id === 'doc-1' ? a.promise : Promise.resolve(documentFor(id))});
  const oldLoad = p.loadDoc('doc-1');
  p.props.docId = 'doc-B';
  await flushRequests();
  assert.equal(p.doc.value.id, 'doc-B');
  a.resolve(documentFor('doc-1'));
  await oldLoad;
  assert.equal(p.doc.value.id, 'doc-B');
  assert.equal(p.fields.value.basic.name, 'doc-B');
  p.fields.value.basic.name = 'B unsaved';
  p.optFields.value = {basic: {name: 'B optimized'}};
  p.props.docId = '';
  assert.equal(p.doc.value, null);
  assert.deepEqual(p.fields.value, {});
  assert.equal(p.optFields.value, null);
  assert.equal(p.dirty.value, false);
  assert.equal(p.errMsg.value, '');
});

test('library rejects stale detail errors and responses from a previous visit to the same id', async (t) => {
  for (const fails of [false, true]) {
    const old = deferred();
    let visits = 0;
    const p = page(t, 'library', {getResume: (id) => ++visits === 1 ? old.promise : Promise.resolve(documentFor(id, 'new visit'))});
    const pending = p.loadDoc('doc-1');
    p.props.docId = '';
    p.props.docId = 'doc-1';
    await flushRequests();
    if (fails) old.reject(Object.assign(new Error('old 404'), {status: 404}));
    else old.resolve(documentFor('doc-1', 'old visit'));
    await pending;
    assert.equal(p.fields.value.basic.name, 'new visit');
    assert.equal(p.errMsg.value, '');
    assert.equal(p.loading.value, false);
  }
});

test('library ignores A optimization sections after props switch to B, without unlocking B optimization', async (t) => {
  const a = deferred(), b = deferred();
  const sectionCalls = [];
  const p = await library(t, {
    getResume: async (id) => documentFor(id), optimizePlan: async () => optimizationPlan(),
    optimizeSection: (body) => { sectionCalls.push(body); return body.fields.basic.name === 'doc-1' ? a.promise : b.promise; },
  });
  const oldOptimize = p.onOptimize();
  await flushRequests();
  assert.equal(sectionCalls.length, 1);
  p.props.docId = 'doc-B';
  assert.equal(p.optFields.value, null);
  assert.deepEqual(p.optResults.value, {});
  assert.equal(p.optBusy.value, false);
  await flushRequests();
  const newOptimize = p.onOptimize();
  await flushRequests();
  a.resolve({status: 'done', optimized: {name: 'A optimized'}});
  await oldOptimize;
  assert.equal(p.doc.value.id, 'doc-B');
  assert.equal(p.fields.value.basic.name, 'doc-B');
  assert.equal(p.optFields.value.basic.name, 'doc-B');
  assert.equal(p.optBusy.value, true);
  assert.equal(sectionCalls.filter((r) => r.fields.basic.name === 'doc-1').length, 1);
  await p.onSave();
  await p.onExport();
  assert.equal(p.calls.length, 0);
  b.resolve({status: 'done', optimized: {name: 'B optimized'}});
  await newOptimize;
  assert.equal(p.optFields.value.basic.name, 'B optimized');
});

test('library ignores stale optimization plans and section failures after leaving the document', async (t) => {
  for (const stage of ['plan', 'section']) {
    const wait = deferred();
    let sections = 0;
    const p = await library(t, {
      getResume: async (id) => documentFor(id),
      optimizePlan: () => stage === 'plan' ? wait.promise : Promise.resolve(optimizationPlan()),
      optimizeSection: () => { sections++; return wait.promise; },
    });
    const pending = p.onOptimize();
    await flushRequests();
    p.props.docId = 'doc-B';
    await flushRequests();
    if (stage === 'plan') wait.resolve(optimizationPlan());
    else wait.reject(new Error('obsolete section failure'));
    await pending;
    assert.equal(p.optFields.value, null);
    assert.equal(p.optPlan.value, null);
    assert.deepEqual(p.optResults.value, {});
    assert.equal(p.errMsg.value, '');
    assert.equal(sections, stage === 'plan' ? 0 : 1);
    await p.onSave();
    assert.equal(p.calls.length, 0);
  }
});

test('library old PATCH cannot export or release the new document save lock', async (t) => {
  for (const fails of [false, true]) {
    const a = deferred(), b = deferred();
    const saves = [];
    const p = await library(t, {
      getResume: async (id) => documentFor(id),
      patchResume: (id) => { saves.push(id); return id === 'doc-1' ? a.promise : b.promise; },
    });
    p.fields.value.basic.name = 'A edit';
    const oldExport = p.onExport();
    p.props.docId = 'doc-B';
    await flushRequests();
    p.fields.value.basic.name = 'B edit';
    const newSave = p.onSave();
    if (fails) a.reject(new Error('old save error'));
    else a.resolve({document: documentFor('doc-1', 'A edit')});
    await oldExport;
    assert.equal(p.doc.value.id, 'doc-B');
    assert.equal(p.fields.value.basic.name, 'B edit');
    assert.equal(p.busy.value, true);
    assert.equal(p.saving.value, true);
    assert.equal(p.errMsg.value, '');
    assert.deepEqual(saves, ['doc-1', 'doc-B']);
    assert.equal(p.calls.length, 0);
    b.resolve({document: documentFor('doc-B', 'B edit')});
    await newSave;
    assert.equal(p.dirty.value, false);
    assert.equal(p.busy.value, false);
  }
});

test('library ignores old PDF results/errors and preserves a newer export lock', async (t) => {
  for (const fails of [false, true]) {
    const a = deferred(), b = deferred();
    const p = await library(t, {
      getResume: async (id) => documentFor(id),
      exportResumePdf: (id) => id === 'doc-1' ? a.promise : b.promise,
    });
    const oldExport = p.onExport();
    p.props.docId = 'doc-B';
    await flushRequests();
    const newExport = p.onExport();
    if (fails) a.reject(new Error('old PDF error'));
    else a.resolve(new Blob(['A PDF']));
    await oldExport;
    assert.equal(p.calls.length, 0);
    assert.equal(p.exporting.value, true);
    assert.equal(p.busy.value, true);
    assert.equal(p.errMsg.value, '');
    b.resolve(new Blob(['B PDF']));
    await newExport;
    assert.equal(p.calls.length, 1);
    assert.equal(p.calls[0][2], 'doc-B');
  }
});

test('library suppresses downloads on unmount and same-id revisits', async (t) => {
  for (const leave of ['unmount', 'revisit']) {
    const pdf = deferred();
    const p = await library(t, {exportResumePdf: () => pdf.promise});
    const exporting = p.onExport();
    if (leave === 'unmount') p.unmount();
    else { p.props.docId = ''; p.props.docId = 'doc-1'; await flushRequests(); }
    pdf.resolve(new Blob(['old PDF']));
    await exporting;
    assert.equal(p.calls.length, 0);
  }
});

test('library ignores old regenerate/restore/duplicate results and errors after navigation', async (t) => {
  for (const operation of ['onRegenerate', 'onRestore', 'onDuplicate']) {
    for (const fails of [false, true]) {
      const wait = deferred();
      const p = await library(t, {
        getResume: async (id) => documentFor(id),
        regenerateResume: () => wait.promise, restoreVersion: () => wait.promise, duplicateResume: () => wait.promise,
      });
      const pending = p[operation]({version: 1});
      p.props.docId = 'doc-B';
      await flushRequests();
      if (fails) wait.reject(new Error('obsolete operation failure'));
      else wait.resolve({document: documentFor('doc-1', 'stale')});
      await pending;
      assert.equal(p.doc.value.id, 'doc-B');
      assert.equal(p.fields.value.basic.name, 'doc-B');
      assert.equal(p.errMsg.value, '');
      assert.equal(p.calls.length, 0);
    }
  }
});

test('library duplicate saves dirty photo and text before copying and navigating', async (t) => {
  let saved = {...initial(), fields: {...initial().fields, _meta: {photo: 'oldphoto'}}};
  let copied;
  const order = [];
  const p = await library(t, {
    getResume: async () => saved,
    patchResume: async (id, body, version) => {
      assert.equal(p.busy.value, true);
      order.push(['save', id, version]);
      saved = {...saved, fields: structuredClone(body.fields), version: version + 1};
      return {document: saved};
    },
    duplicateResume: async (id) => {
      assert.equal(p.busy.value, true);
      order.push(['copy', id]);
      copied = {...saved, id: 'copy-1'};
      return {document: copied};
    },
  });
  p.fields.value.basic.name = 'current text';
  p.fields.value._meta.photo = 'currentphoto';
  await p.onDuplicate();
  assert.deepEqual(order, [['save', 'doc-1', 3], ['copy', 'doc-1']]);
  assert.deepEqual(copied.fields, {basic: {name: 'current text'}, _meta: {photo: 'currentphoto'}});
  assert.deepEqual(p.calls, [['emit', 'nav', '#/resume/library/copy-1']]);
  assert.equal(p.busy.value, false);
});

test('library dirty restore does not prune the selected oldest history before restoration', async (t) => {
  let saved = {...initial(), version: 11};
  let history = Array.from({length: 10}, (_, index) => ({...initial(), version: index + 1, fields: {basic: {name: `history-${index + 1}`}, _meta: {photo: `photo-${index + 1}`}}}));
  const calls = [];
  const snapshot = () => { history.push(structuredClone(saved)); history = history.slice(-10); };
  const p = await library(t, {
    getResume: async () => saved,
    confirm: () => { calls.push('confirm'); return true; },
    patchResume: async (id, body) => {
      calls.push('patch'); snapshot();
      saved = {...saved, version: saved.version + 1, fields: body.fields};
      return {document: saved};
    },
    restoreVersion: async (id, version) => {
      calls.push('restore');
      const target = history.find(item => item.version === version);
      if (!target) throw Object.assign(new Error('pruned'), {status: 404});
      snapshot();
      saved = {...target, version: saved.version + 1};
      return {document: saved};
    },
  });
  p.fields.value._meta = {photo: 'unsavedphoto'};
  await p.onRestore({version: 1});
  assert.deepEqual(calls, []);
  assert.equal(history[0].version, 1);
  assert.equal(p.fields.value._meta.photo, 'unsavedphoto');
  assert.equal(p.dirty.value, true);
  assert.match(p.errMsg.value, /未保存/);
  assert.match(p.errMsg.value, /10/);
  await p.loadDoc('doc-1');
  await p.onRestore({version: 1});
  assert.deepEqual(calls, ['confirm', 'restore']);
  assert.equal(p.fields.value.basic.name, 'history-1');
  assert.equal(p.dirty.value, false);
});

for (const operation of ['onDuplicate', 'onRegenerate', 'onRestore']) {
  test(`library ${operation} rejects unadopted optimization before saving or confirming`, async (t) => {
    const attempted = [];
    const replacement = async () => { attempted.push('replace'); return {document: initial()}; };
    const p = await library(t, {
      confirm: () => { attempted.push('confirm'); return true; },
      duplicateResume: replacement, regenerateResume: replacement, restoreVersion: replacement,
    });
    p.fields.value._meta = {photo: 'currentphoto'};
    p.optFields.value = {basic: {name: 'optimized'}, _meta: {photo: 'oldphoto'}};
    p.doc.value.stale_sources = [{field_path: 'basic.name'}];
    await p[operation]({version: 1});
    assert.deepEqual(attempted, []);
    assert.deepEqual(p.calls, []);
    assert.match(p.errMsg.value, /采纳.*放弃/);
    assert.equal(p.fields.value._meta.photo, 'currentphoto');
    assert.equal(p.optFields.value._meta.photo, 'oldphoto');
    assert.equal(p.dirty.value, true);
    assert.equal(p.busy.value, false);
  });

}

for (const operation of ['onDuplicate', 'onRegenerate']) {
  test(`library ${operation} stops on PATCH conflict/failure without losing pending photo`, async (t) => {
    for (const status of [409, 500]) {
      const attempted = [];
      const replacement = async () => { attempted.push('replace'); return {document: initial()}; };
      const p = await library(t, {
        patchResume: async () => { attempted.push('save'); throw Object.assign(new Error('offline'), {status}); },
        duplicateResume: replacement, regenerateResume: replacement, restoreVersion: replacement,
      });
      p.fields.value._meta = {photo: 'currentphoto'};
      await p[operation]({version: 1});
      assert.deepEqual(attempted, ['save']);
      assert.deepEqual(p.calls, []);
      assert.equal(p.fields.value._meta.photo, 'currentphoto');
      assert.equal(p.dirty.value, true);
      assert.match(p.errMsg.value, status === 409 ? /版本冲突/ : /保存失败/);
      assert.equal(p.busy.value, false);
      assert.equal(p.saving.value, false);
    }
  });

  test(`library ${operation} stops if newer edits remain after PATCH and retains action locks`, async (t) => {
    const wait = deferred();
    const attempted = [];
    const replacement = async () => { attempted.push('replace'); return {document: initial()}; };
    const p = await library(t, {
      patchResume: () => { attempted.push('save'); return wait.promise; },
      duplicateResume: replacement, regenerateResume: replacement, restoreVersion: replacement,
    });
    p.fields.value._meta = {photo: 'savingphoto'};
    const pending = p[operation]({version: 1});
    assert.equal(p.saving.value, true);
    await p.onDuplicate(); await p.onRegenerate(); await p.onRestore({version: 1});
    await p.onSave(); await p.onExport(); await p.onOptimize();
    p.fields.value._meta.photo = 'newerphoto';
    wait.resolve({document: {...initial(), version: 4, fields: {...initial().fields, _meta: {photo: 'savingphoto'}}}});
    await pending;
    assert.deepEqual(attempted, ['save']);
    assert.deepEqual(p.calls, []);
    assert.equal(p.fields.value._meta.photo, 'newerphoto');
    assert.equal(p.dirty.value, true);
    assert.match(p.errMsg.value, /未保存.*保存/);
    assert.equal(p.busy.value, false);
  });

  test(`library ${operation} ignores late PATCH success/error after navigation and same-id revisit`, async (t) => {
    for (const revisit of [false, true]) {
      for (const fails of [false, true]) {
        const old = deferred(), current = deferred();
        const attempted = [];
        let saves = 0;
        const replacement = async () => { attempted.push('replace'); return {document: initial()}; };
        const p = await library(t, {
          getResume: async (id) => documentFor(id),
          patchResume: () => { saves++; return saves === 1 ? old.promise : current.promise; },
          duplicateResume: replacement, regenerateResume: replacement, restoreVersion: replacement,
        });
        p.fields.value._meta = {photo: 'oldvisitphoto'};
        const pending = p[operation]({version: 1});
        assert.equal(saves, 1);
        p.props.docId = '';
        p.props.docId = revisit ? 'doc-1' : 'doc-B';
        await flushRequests();
        p.fields.value._meta = {photo: 'currentphoto'};
        const newSave = p.onSave();
        if (fails) old.reject(Object.assign(new Error('old conflict'), {status: 409}));
        else old.resolve({document: documentFor('doc-1', 'old visit')});
        await pending;
        assert.deepEqual(attempted, []);
        assert.deepEqual(p.calls, []);
        assert.equal(p.doc.value.id, p.props.docId);
        assert.equal(p.fields.value._meta.photo, 'currentphoto');
        assert.equal(p.busy.value, true);
        assert.equal(p.saving.value, true);
        assert.equal(p.errMsg.value, '');
        current.resolve({document: {...documentFor(p.props.docId), fields: JSON.parse(JSON.stringify(p.fields.value))}});
        await newSave;
        assert.equal(p.dirty.value, false);
        assert.equal(p.busy.value, false);
      }
    }
  });
}

test('library regenerate persists current photo/text before replacing the document', async (t) => {
  let saved = {...initial(), fields: {...initial().fields, _meta: {photo: 'oldphoto'}}};
  const order = [];
  const p = await library(t, {
    getResume: async () => saved,
    patchResume: async (id, body, version) => {
      order.push(['save', id, version]);
      saved = {...saved, fields: structuredClone(body.fields), version: version + 1};
      return {document: saved};
    },
    regenerateResume: async (id, body) => {
      order.push(['regenerate', id]);
      assert.equal(body.keep_manual, false);
      return {document: {...saved, fields: {basic: {name: 'regenerated'}, _meta: saved.fields._meta}}};
    },
  });
  p.keepManual.value = false;
  p.fields.value.basic.name = 'current text';
  p.fields.value._meta.photo = 'currentphoto';
  await p.onRegenerate();
  assert.deepEqual(order, [['save', 'doc-1', 3], ['regenerate', 'doc-1']]);
  assert.equal(saved.fields.basic.name, 'current text');
  assert.equal(saved.fields._meta.photo, 'currentphoto');
  assert.equal(p.fields.value.basic.name, 'regenerated');
  assert.equal(p.fields.value._meta.photo, 'currentphoto');
  assert.equal(p.dirty.value, false);
  assert.equal(p.busy.value, false);
});

test('library ignores old version lists after navigation', async (t) => {
  const old = deferred();
  const p = await library(t, {
    getResume: async (id) => documentFor(id),
    listVersions: (id) => id === 'doc-1' ? old.promise : Promise.resolve({versions: [{id: 'b-v2', version: 2}]}),
  });
  p.props.docId = 'doc-B';
  await flushRequests();
  old.resolve({versions: [{id: 'a-v1', version: 1}]});
  await flushRequests();
  assert.deepEqual(p.versions.value, [{id: 'b-v2', version: 2}]);
});

test('library discards optimization without changing original edits, saved baseline or server state', async (t) => {
  for (const edited of [false, true]) {
    const p = await library(t, {
      optimizePlan: async () => optimizationPlan(),
      optimizeSection: async () => ({status: 'done', optimized: {name: 'optimized'}, change_log: [{before: 'old', after: 'new'}]}),
    });
    if (edited) p.fields.value.basic.name = 'unsaved original';
    const original = JSON.stringify(p.fields.value);
    await p.onOptimize();
    await p.onExport(); // creates the obsolete adoption warning
    p.exportProblems.value = [{reason: 'obsolete'}];
    assert.equal(typeof p.discardOptimized, 'function');
    p.discardOptimized();
    assert.equal(JSON.stringify(p.fields.value), original);
    assert.equal(p.dirty.value, edited);
    assert.equal(p.doc.value.fields.basic.name, '小林');
    assert.equal(p.optFields.value, null);
    assert.equal(p.optPlan.value, null);
    assert.deepEqual(p.optResults.value, {});
    assert.equal(p.errMsg.value, '');
    assert.deepEqual(p.exportProblems.value, []);
    assert.equal(p.calls.length, 0);
    await p.onExport();
    assert.deepEqual(p.calls.map((c) => c[0]), edited ? ['save', 'pdf', 'download'] : ['pdf', 'download']);
  }
});

test('wizard discards optimization back to original preview without overwriting local drafts', async (t) => {
  const stored = new Map();
  const writes = [];
  const p = wizard(t, {
    saveDraft: (key, body) => { writes.push(key); stored.set(key, structuredClone(body)); }, loadDraft: (key) => stored.get(key),
    optimizePlan: async () => optimizationPlan(),
    optimizeSection: async () => ({status: 'done', optimized: {name: 'optimized'}, placeholders: [{field_path: 'basic.name'}]}),
  });
  await nextTick();
  p.step.value = 3;
  await p.doOptimize();
  const original = JSON.stringify(p.fields.value), saveState = p.saveState.value, writeCount = writes.length;
  p.errMsg.value = 'obsolete warning'; p.exportProblems.value = [{reason: 'obsolete'}];
  assert.equal(typeof p.discardOptimized, 'function');
  p.discardOptimized();
  await nextTick();
  assert.equal(p.step.value, 2);
  assert.equal(JSON.stringify(p.fields.value), original);
  assert.equal(p.optFields.value, null);
  assert.equal(p.plan.value, null);
  assert.deepEqual(p.results.value, {});
  assert.equal(p.errMsg.value, '');
  assert.deepEqual(p.exportProblems.value, []);
  assert.equal(p.saveState.value, saveState);
  assert.equal(writes.length, writeCount);
  await p.exportPrint();
  assert.equal(JSON.stringify(p.calls[0][1].fields), original);
});

test('pending photo blocks save/export/optimization/regeneration/restore without disabling its own form', async (t) => {
  for (const kind of ['library', 'wizard']) {
    let writes = 0;
    const p = kind === 'library' ? await library(t) : wizard(t, {saveDraft: () => writes++});
    await nextTick();
    assert.equal(typeof p.onPhotoBusy, 'function');
    const event = {busy: true, context: p.photoContext.value, request: Symbol('upload')};
    p.onPhotoBusy(event);
    assert.equal(p.photoBusy.value, true);
    const before = writes;
    p.fields.value.basic.name = 'edit while encoding';
    if (kind === 'library') {
      await p.onSave(); await p.onExport(); await p.onOptimize(); await p.onRegenerate(); await p.onRestore({version: 1});
    } else {
      await p.exportPrint(); await p.doOptimize(); await p.doAutofill(); p.saveLocalDraft();
      assert.equal(p.locked.value, true);
      assert.equal(p.formDisabled.value, false, 'photo busy must not cancel its own form');
    }
    await nextTick();
    assert.equal(p.calls.length, 0); assert.equal(writes, before);
    p.onPhotoBusy({...event, busy: false});
    await nextTick();
    assert.equal(p.photoBusy.value, false);
    if (kind === 'wizard') assert.ok(writes > before, 'deferred autosave resumes after photo completion');
  }
});

test('late photo busy events cannot release newer request or another document/ABA lock', async (t) => {
  const p = await library(t, {getResume: async (id) => documentFor(id)});
  assert.equal(typeof p.onPhotoBusy, 'function');
  const old = {busy: true, context: p.photoContext.value, request: Symbol('old')};
  p.onPhotoBusy(old);
  p.props.docId = ''; p.props.docId = 'doc-1';
  await flushRequests();
  assert.equal(p.photoBusy.value, false);
  const current = {busy: true, context: p.photoContext.value, request: Symbol('new')};
  assert.notEqual(current.context, old.context);
  p.onPhotoBusy(current);
  p.onPhotoBusy({...old, busy: false});
  p.onPhotoBusy({...old, busy: true});
  p.onPhotoBusy({...current, busy: false, request: Symbol('other form')});
  assert.equal(p.photoBusy.value, true);
  p.onPhotoBusy({...current, busy: false});
  assert.equal(p.photoBusy.value, false);
});

test('photo and manual metadata survive optimization adopt/discard, local readback and reload', async (t) => {
  const meta = {photo: 'data:image/jpeg;base64,PRIVATE_PHOTO', manual_override: ['basic.name']};
  for (const kind of ['library', 'wizard']) {
    const stored = new Map();
    const overrides = {
      saveDraft: (key, body) => stored.set(key, JSON.parse(JSON.stringify(body))), loadDraft: (key) => stored.get(key),
      optimizePlan: async () => optimizationPlan(),
      optimizeSection: async () => ({status: 'done', optimized: {name: 'optimized'}}),
    };
    const p = kind === 'library' ? await library(t, overrides) : wizard(t, overrides);
    p.fields.value._meta = structuredClone(meta);
    await nextTick();
    if (kind === 'wizard') {
      assert.equal(p.saveState.value, '已保存到本机');
      assert.deepEqual(stored.get('draft').fields._meta, meta);
      const reload = page(t, 'wizard', overrides);
      await reload.loadTemplate();
      assert.deepEqual(JSON.parse(JSON.stringify(reload.fields.value._meta)), meta);
    }
    await (p.onOptimize || p.doOptimize)();
    assert.deepEqual(JSON.parse(JSON.stringify(p.optFields.value._meta)), meta);
    p.discardOptimized();
    assert.deepEqual(JSON.parse(JSON.stringify(p.fields.value._meta)), meta);
    await (p.onOptimize || p.doOptimize)();
    (p.adoptOptimized || p.adoptAll)();
    assert.deepEqual(JSON.parse(JSON.stringify(p.fields.value._meta)), meta);
  }
});

// Render the actual Vue template on the server: no browser or screenshot script.
async function markup(kind, state, forms = []) {
  const {descriptor} = parse(sources[kind]);
  const compiled = compileTemplate({source: descriptor.template.content, filename: `${kind}.vue`, id: kind});
  assert.deepEqual(compiled.errors, []);
  const code = compiled.code.replace(/import \{([^}]+)\} from "vue"/g,
    (_, imports) => `const {${imports.replace(/\s+as\s+/g, ':')}} = Vue;`).replace('export function render', 'function render');
  const render = new Function('Vue', `${code}; return render;`)(Vue);
  const app = Vue.createSSRApp({
    setup: () => state, render,
    components: {
      ResumeFieldsForm: {
        props: ['modelValue', 'disabled'], emits: ['update:modelValue', 'photo-busy'],
        setup(props, {emit}) {
          forms.push({modelValue: props.modelValue, disabled: props.disabled, update: (fields) => emit('update:modelValue', fields)});
          return () => Vue.h('div', {class: 'form-stub'});
        },
      },
      ResumePreview: {props: ['fields'], render() { return Vue.h('div', {class: 'paper'}, JSON.stringify(this.fields)); }},
    },
  });
  // The isolated setup harness only exposes the bindings relevant to these tests.
  app.config.warnHandler = () => {};
  return renderToString(app);
}

for (const editStep of [1, 2]) {
  for (const edit of ['replace photo', 'remove photo', 'change text']) {
    test(`wizard step ${editStep} form ${edit} invalidates old optimization before adoption/export`, async (t) => {
      const stored = new Map();
      const p = wizard(t, {
        saveDraft: (key, body) => stored.set(key, JSON.parse(JSON.stringify(body))), loadDraft: (key) => stored.get(key),
        optimizePlan: async () => optimizationPlan(),
        optimizeSection: async () => ({status: 'done', optimized: {name: 'old optimized text'}}),
      });
      p.fields.value._meta = {photo: 'oldphoto', manual_override: ['basic.name']};
      p.step.value = 3;
      await p.doOptimize();
      assert.equal(p.optFields.value._meta.photo, 'oldphoto');
      assert.equal(p.optFields.value.basic.name, 'old optimized text');
      p.step.value = editStep;
      p.errMsg.value = 'obsolete export warning'; p.exportProblems.value = [{reason: 'obsolete'}];
      const forms = [];
      const state = {...p, filled: true, convo: [], sessions: [], navSections: [], placeholders: [], pendingQuestions: [], STEP_CN: {}};
      await markup('wizard', state, forms);
      assert.equal(forms.length, 1);
      assert.deepEqual(forms[0].modelValue, p.fields.value);
      const next = JSON.parse(JSON.stringify(p.fields.value));
      if (edit === 'remove photo') delete next._meta.photo;
      else if (edit === 'replace photo') next._meta.photo = 'currentphoto';
      else next.basic.name = 'current text';
      const photoEvent = {busy: true, context: p.photoContext.value, request: Symbol('photo')};
      if (edit === 'replace photo') p.onPhotoBusy(photoEvent);
      const atAssignment = [];
      const stop = watch(p.fields, () => atAssignment.push([p.optFields.value, p.plan.value, {...p.results.value}, [...p.exportProblems.value], p.errMsg.value]), {flush: 'sync'});
      forms[0].update(next); // Executes the actual handler bound by each compiled page form.
      stop();
      assert.equal(p.optFields.value, null);
      assert.deepEqual(atAssignment, [[null, null, {}, [], '']], 'invalidate stale state before assigning/autosaving fields');
      assert.deepEqual(JSON.parse(JSON.stringify(p.fields.value)), next);
      if (edit === 'replace photo') {
        assert.equal(p.photoBusy.value, true, 'form update must not reset its active photo request');
        p.onPhotoBusy({...photoEvent, busy: false});
      }
      await nextTick();
      assert.deepEqual(stored.get('draft').fields, next);
      p.step.value = 3;
      p.adoptAll();
      assert.deepEqual(JSON.parse(JSON.stringify(p.fields.value)), next);
      assert.equal(stored.has('opt'), false);
      const html = await markup('wizard', state);
      assert.doesNotMatch(html, /old optimized text|采纳优化稿|导出优化稿/);
      if (edit !== 'change text') assert.doesNotMatch(html, /oldphoto/);
      await p.exportPrint();
      assert.deepEqual(p.calls[0][1].fields, next);
    });
  }
}

test('both wizard forms bind the explicit edit handler and respect non-photo locks', async (t) => {
  const forms = [...sources.wizard.matchAll(/<ResumeFieldsForm\b[\s\S]*?\/>/g)];
  assert.equal(forms.length, 2);
  for (const [form] of forms) {
    assert.match(form, /:model-value="fields"/);
    assert.match(form, /@update:model-value="onFieldsUpdate"/);
    assert.doesNotMatch(form, /v-model/);
  }
  const p = wizard(t);
  assert.equal(typeof p.onFieldsUpdate, 'function');
  for (const lock of ['busy', 'optBusy', 'exporting']) {
    p[lock].value = true;
    p.optFields.value = {basic: {name: 'optimized'}};
    p.onFieldsUpdate({basic: {name: 'blocked edit'}});
    assert.equal(p.fields.value.basic.name, '小林');
    assert.equal(p.optFields.value.basic.name, 'optimized');
    p[lock].value = false;
  }
});

test('wizard saving/adopting fields does not invalidate optimization as if it were a form edit', async (t) => {
  const stored = new Map();
  const p = wizard(t, {
    saveDraft: (key, body) => stored.set(key, JSON.parse(JSON.stringify(body))), loadDraft: (key) => stored.get(key),
    optimizePlan: async () => optimizationPlan(),
    optimizeSection: async () => ({status: 'done', optimized: {name: 'optimized'}}),
  });
  p.fields.value._meta = {photo: 'currentphoto', manual_override: ['basic.name']};
  await p.doOptimize();
  const optimized = JSON.parse(JSON.stringify(p.optFields.value));
  p.saveLocalDraft();
  await nextTick();
  assert.deepEqual(JSON.parse(JSON.stringify(p.optFields.value)), optimized);
  p.adoptAll();
  await nextTick();
  assert.deepEqual(stored.get('opt').fields, optimized);
  assert.deepEqual(stored.get('draft').fields, optimized);
  assert.deepEqual(JSON.parse(JSON.stringify(p.fields.value)), optimized);
  assert.equal(p.saveState.value, '已保存到本机');
});

test('both editors expose escaped, collapsible array evidence outside paper and PDF payloads', async (t) => {
  const evidence = {'basic.<img>': [
    {quote: '<script>private quote</script>', session_id: 'javascript:<session>', msg_id: 0, fact_id: '<fact>', fact_status: 'confirmed', url: 'javascript:alert(1)'},
    {quote: 'second private quote', msg_id: 'm2'},
  ]};
  for (const kind of ['library', 'wizard']) {
    const p = kind === 'library' ? await library(t) : wizard(t);
    if (kind === 'library') p.doc.value.evidence_map = evidence;
    else p.evidence.value = evidence;
    const state = {...p, hasToken: true, staleList: [], docs: [], placeholders: [], pendingQuestions: [], navSections: [], STEP_CN: {}, mobilePanel: 'edit'};
    for (const step of kind === 'wizard' ? [2, 3] : [2]) {
      if (p.step) p.step.value = step;
      const html = await markup(kind, state);
      const panel = html.match(/<details[^>]*class="evidence-panel"[\s\S]*?<\/details>/)?.[0];
      assert.ok(panel, `${kind} step ${step} must expose an evidence disclosure`);
      assert.match(panel, /<summary[^>]*>[^<]*出处/);
      assert.doesNotMatch(panel, /\sopen(?:=|\s|>)/);
      assert.match(panel, /basic\.&lt;img&gt;/);
      assert.match(panel, /&lt;script&gt;private quote&lt;\/script&gt;/);
      assert.match(panel, /second private quote/);
      assert.match(panel, /session_id[\s\S]*javascript:&lt;session&gt;/);
      assert.match(panel, /msg_id[\s\S]*0/);
      assert.match(panel, /fact_id[\s\S]*&lt;fact&gt;/);
      assert.doesNotMatch(panel, /<(?:a|script|img)\b|href=/);
      assert.match(html, /<aside\b[\s\S]*class="evidence-panel"[\s\S]*<\/aside>/);
      assert.doesNotMatch(html.match(/<div class="paper"[^>]*>[\s\S]*?<\/div>/)?.[0] || '', /private quote|session_id/);
    }
    assert.match(sources[kind], /@media print\s*\{[\s\S]*?\.evidence-panel[^{}]*\{\s*display:\s*none\s*!important/);
    await (p.onExport || p.exportPrint)();
    assert.doesNotMatch(JSON.stringify(p.calls), /private quote|evidence_map|session_id/);
    p.optFields.value = {basic: {name: 'optimized'}};
    if (p.step) p.step.value = 3;
    const html = await markup(kind, state);
    assert.match(html, /<button[^>]*>放弃优化稿<\/button>/);
  }
});

test('pending photo disables action markup but leaves the photo form enabled in both editors', async (t) => {
  for (const kind of ['library', 'wizard']) {
    const p = kind === 'library' ? await library(t) : wizard(t);
    p.onPhotoBusy({busy: true, context: p.photoContext.value, request: Symbol('upload')});
    const html = await markup(kind, {...p, filled: true, hasToken: true, staleList: [], docs: [], placeholders: [], pendingQuestions: [], navSections: [], STEP_CN: {}, mobilePanel: 'edit'});
    assert.match(html, /<button[^>]*disabled[^>]*>\s*导出 PDF\s*<\/button>/);
    const fieldset = html.match(/<fieldset[^>]*class="(?:lib|wiz)-fields"[^>]*>/)?.[0];
    assert.ok(fieldset); assert.doesNotMatch(fieldset, /disabled/);
    for (const form of sources[kind].matchAll(/<ResumeFieldsForm\b[\s\S]*?\/>/g)) {
      assert.match(form[0], /:context="photoContext"/);
      assert.match(form[0], /@photo-busy="onPhotoBusy"/);
      assert.match(form[0], /:disabled="(?![^\"]*photoBusy)[^\"]+"/);
    }
  }
});

test('optimization sends contract fields without internal metadata and preserves the saved snapshot', async () => {
  const calls = [];
  const a = evaluate(sources.resume, {req: async (url, init) => { calls.push({url, ...init}); return {}; }}, ['optimizePlan', 'optimizeSection']);
  const payload = {template_version: 't1', section: 'summary', fields: {summary: {text: '原稿'}, _meta: {manual_override: ['summary.text'], photo: 'data:image/jpeg;base64,PRIVATE_PHOTO'}}};
  const before = JSON.stringify(payload);
  await a.optimizePlan(payload);
  await a.optimizeSection(payload);
  assert.equal(calls.length, 2);
  for (const call of calls) {
    assert.deepEqual(call.body.fields, {summary: {text: '原稿'}});
    assert.equal(call.body.template_version, 't1');
  }
  assert.equal(JSON.stringify(payload), before);
});

function api(kind, response, sessionToken = 'session-secret') {
  const calls = [];
  const bindings = {
    API_BASE: 'https://api.example.test', getAnonId: () => 'anon', getSessionToken: () => sessionToken,
    authHeaders: (id, extra) => ({'X-Anon-Id':'anon', ...(id && sessionToken ? {'X-Session-Token':sessionToken} : {}), ...extra}),
    localStorage: {getItem: () => 'owner-secret'},
    fetch: async (url, init) => { calls.push({url,...init}); return response; },
  };
  return {...evaluate(sources[kind], bindings, ['exportResumePdf','exportDraftPdf']), calls};
}
const pdfResponse = () => new Response(new Blob(['%PDF-test'], {type:'application/pdf'}));

test('v3 PDF API posts version/layout with owner+anon headers and no credentials in URL', async () => {
  const a = api('v3', pdfResponse());
  const blob = await a.exportResumePdf('doc/id', {version:9,layout:'modern'});
  assert.equal(blob.type, 'application/pdf');
  assert.equal(a.calls[0].url, 'https://api.example.test/api/v3/resumes/doc%2Fid/pdf');
  assert.equal(a.calls[0].method, 'POST');
  assert.equal(a.calls[0].headers['X-Owner-Token'], 'owner-secret');
  assert.equal(a.calls[0].headers['X-Anon-Id'], 'anon');
  assert.deepEqual(JSON.parse(a.calls[0].body), {version:9,layout:'modern'});
});

test('v2 PDF API supports a credential-free snapshot and explicit session headers', async () => {
  const body = {template_version:'t1', fields:{}, layout:'classic'};
  const plain = api('resume', pdfResponse());
  await plain.exportDraftPdf(body);
  assert.equal(plain.calls[0].url, 'https://api.example.test/api/v2/resume/pdf');
  assert.equal(plain.calls[0].headers['X-Anon-Id'], 'anon');
  assert.equal(plain.calls[0].headers['X-Owner-Token'], undefined);
  assert.equal(plain.calls[0].headers['X-Session-Token'], undefined);
  const withSession = api('resume', pdfResponse());
  await withSession.exportDraftPdf({...body,session_id:'s1'});
  assert.equal(withSession.calls[0].headers['X-Session-Token'], 'session-secret');
  const missing = api('resume', pdfResponse(), '');
  await assert.rejects(() => missing.exportDraftPdf({...body,session_id:'s1'}), /凭据/);
  assert.equal(missing.calls.length, 0);
});

test('PDF APIs preserve structured/string errors and reject non-PDF successful responses', async () => {
  const issues = [{field_path:'basic.name',label:'姓名',reason:'缺失'}];
  for (const kind of ['v3','resume']) {
    const run = (a) => kind === 'v3' ? a.exportResumePdf('d',{version:1,layout:'classic'}) : a.exportDraftPdf({fields:{},layout:'classic',template_version:'t1'});
    const bad = api(kind, Response.json({detail:{code:'invalid_fields',message:'请补全',issues}}, {status:422}));
    await assert.rejects(() => run(bad), (e) => e.status === 422 && e.code === 'invalid_fields' && e.message === '请补全' && assert.deepEqual(e.issues, issues) === undefined);
    await assert.rejects(() => run(api(kind, Response.json({detail:'服务未就绪'}, {status:503}))), /服务未就绪/);
    await assert.rejects(() => run(api(kind, Response.json({ok:true}))), /PDF/);
  }
});

test('safe download sanitizes names and revokes object URLs, including click failures', async () => {
  let source = '';
  try { source = await read('../src/utils/resumeDownload.js'); } catch (e) { if (e.code !== 'ENOENT') throw e; }
  for (const fail of [false, true]) {
    const events = [];
    const link = {click() { events.push('click'); if (fail) throw new Error('click failed'); }, remove() { events.push('remove'); }};
    const bindings = {
      URL: {createObjectURL: () => { events.push('create'); return 'blob:private'; }, revokeObjectURL: (url) => events.push(['revoke',url])},
      document: {createElement: () => link, body:{appendChild: () => events.push('append')}},
      setTimeout: (fn) => { events.push('defer'); fn(); },
    };
    const helper = evaluate(source, bindings, ['downloadResumePdf']);
    assert.equal(typeof helper.downloadResumePdf, 'function');
    const run = () => helper.downloadResumePdf(new Blob(['pdf'], {type:'application/pdf'}), '../危险\\名字\u202e\n.pdf.exe');
    if (fail) assert.throws(run, /click failed/); else run();
    assert.equal(link.href, 'blob:private');
    assert.match(link.download, /\.pdf$/);
    assert.ok(!/[\\/\r\n\u202e]/.test(link.download));
    assert.ok(!link.download.startsWith('.'));
    assert.equal(link.download.includes('.exe'), false);
    assert.ok(events.includes('remove'));
    assert.deepEqual(events.at(-1), ['revoke','blob:private']);
  }
});

test('both SFCs compile, expose Chinese layouts/mobile modes, and keep editor annotations off paper', () => {
  for (const [kind, source] of Object.entries(sources).filter(([k]) => ['library','wizard'].includes(k))) {
    const {descriptor, errors} = parse(source);
    assert.deepEqual(errors, []);
    const script = compileScript(descriptor, {id:kind});
    const template = compileTemplate({source:descriptor.template.content, filename:`${kind}.vue`, id:kind, compilerOptions:{bindingMetadata:script.bindings}});
    assert.deepEqual(template.errors, []);
    assert.match(source, /中文经典/);
    assert.match(source, /中文现代/);
    assert.match(source, /编辑/);
    assert.match(source, /预览/);
    assert.match(source, /aria-pressed/);
    for (const preview of source.matchAll(/<ResumePreview\b[\s\S]*?\/>/g)) {
      assert.match(preview[0], /:layout="layout"/);
      assert.doesNotMatch(preview[0], /:(gaps|compare|focus|evidence)=/);
    }
    assert.doesNotMatch(source, /标绿|downloadElementAsPdf/);
  }
});
