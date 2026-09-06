import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/bookingExtras.ts', import.meta.url), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const extras = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

test('Natural adds $100 only when explicitly selected', () => {
  assert.equal(extras.bookingTotal(225, []), 225);
  assert.equal(extras.bookingTotal(225, ['natural']), 325);
  assert.equal(extras.bookingTotal(225, ['unknown']), 225);
});
