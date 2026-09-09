import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const navigationSource = await readFile(new URL('../src/messageReviewNavigation.ts', import.meta.url), 'utf8');
const transpiled = ts.transpileModule(navigationSource, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const navigation = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);
const viewSource = await readFile(new URL('../src/SmsTriageDashboard.tsx', import.meta.url), 'utf8');
const apiSource = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8');

test('message review navigation handles first, middle, last, and empty boundaries', () => {
  assert.deepEqual(navigation.getMessageReviewNavigation([], null), {
    previousIndex: null, nextIndex: null, position: null, total: 0,
  });
  assert.deepEqual(navigation.getMessageReviewNavigation(['a', 'b', 'c'], 'a'), {
    previousIndex: null, nextIndex: 1, position: 1, total: 3,
  });
  assert.deepEqual(navigation.getMessageReviewNavigation(['a', 'b', 'c'], 'b'), {
    previousIndex: 0, nextIndex: 2, position: 2, total: 3,
  });
  assert.deepEqual(navigation.getMessageReviewNavigation(['a', 'b', 'c'], 'c'), {
    previousIndex: 1, nextIndex: null, position: 3, total: 3,
  });
});

test('a cleared item retains its former position as a usable navigation anchor', () => {
  assert.deepEqual(navigation.getMessageReviewNavigation(['b', 'c'], 'a', 0), {
    previousIndex: null, nextIndex: 0, position: null, total: 2,
  });
  assert.deepEqual(navigation.getMessageReviewNavigation(['a', 'c'], 'b', 1), {
    previousIndex: 0, nextIndex: 1, position: null, total: 2,
  });
  assert.deepEqual(navigation.getMessageReviewNavigation(['a', 'b'], 'c', 2), {
    previousIndex: 1, nextIndex: null, position: null, total: 2,
  });
});

test('review controls are explicit, accessible, and refresh list and detail', () => {
  assert.match(viewSource, /aria-label="Previous message"/);
  assert.match(viewSource, /aria-label="Next message"/);
  assert.match(viewSource, /disabled=\{navigation\.previousIndex === null\}/);
  assert.match(viewSource, /disabled=\{navigation\.nextIndex === null\}/);
  assert.match(viewSource, /aria-label="Clear all flags from this message"/);
  assert.match(viewSource, /Promise\.all\(\[fetchThreadsList\(\), fetchThreadDetail\(selectedThreadId, true\)\]\)/);
  assert.match(apiSource, /\/api\/threads\/\$\{id\}\/review-flags/);
});
