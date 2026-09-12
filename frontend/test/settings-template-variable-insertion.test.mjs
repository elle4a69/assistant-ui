import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const helperSource = await readFile(new URL('../src/templateVariableInsertion.ts', import.meta.url), 'utf8');
const settingsSource = await readFile(new URL('../src/SettingsView.tsx', import.meta.url), 'utf8');
const transpiled = ts.transpileModule(helperSource, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { applyVariableTokenAtSelection } = await import(
  `data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`
);

function target(value, selectionStart, selectionEnd, apply) {
  return {
    element: { value, selectionStart: 0, selectionEnd: 0 },
    apply,
    selectionStart,
    selectionEnd,
  };
}

test('booking confirmation replaces its tracked selection without changing the previous prompt', () => {
  let prompt = 'Previous prompt';
  let confirmation = 'Hi selected text!';
  let activeTarget = target(prompt, 8, 8, value => { prompt = value; });

  activeTarget = target(confirmation, 3, 16, value => { confirmation = value; });
  const caret = applyVariableTokenAtSelection(activeTarget, 'name');

  assert.equal(confirmation, 'Hi {name}!');
  assert.equal(prompt, 'Previous prompt');
  assert.equal(caret, 9);
  assert.equal(activeTarget.selectionStart, 9);
  assert.equal(activeTarget.selectionEnd, 9);
  assert.match(settingsSource, /aria-label="Booking SMS confirmation template"[\s\S]*?onFocus=\{rememberVariableTarget\(setSmsTemplate\)\}[\s\S]*?onSelect=\{rememberVariableTarget\(setSmsTemplate\)\}/);
});

test('booking reminder inserts at its tracked caret without changing the previous prompt', () => {
  let prompt = 'Previous prompt';
  let reminder = 'Reminder: tomorrow';
  let activeTarget = target(prompt, 8, 8, value => { prompt = value; });

  activeTarget = target(reminder, 10, 10, value => { reminder = value; });
  const caret = applyVariableTokenAtSelection(activeTarget, 'time');

  assert.equal(reminder, 'Reminder: {time}tomorrow');
  assert.equal(prompt, 'Previous prompt');
  assert.equal(caret, 16);
  assert.equal(activeTarget.selectionStart, 16);
  assert.equal(activeTarget.selectionEnd, 16);
  assert.match(settingsSource, /aria-label="Booking reminder template"[\s\S]*?onFocus=\{rememberVariableTarget\(\(value\) => setBookingReminder\(current => \(\{ \.\.\.current, template: value \}\)\)\)\}[\s\S]*?onSelect=\{rememberVariableTarget\(\(value\) => setBookingReminder\(current => \(\{ \.\.\.current, template: value \}\)\)\)\}/);
});

test('existing shared prompt editors continue to track focus and selection', () => {
  assert.match(settingsSource, /value=\{systemPrompt\}[\s\S]*?onFocus=\{rememberVariableTarget\(setSystemPrompt\)\}[\s\S]*?onSelect=\{rememberVariableTarget\(setSystemPrompt\)\}/);
  assert.match(settingsSource, /value=\{userPrompt\}[\s\S]*?onFocus=\{rememberVariableTarget\(setUserPrompt\)\}[\s\S]*?onSelect=\{rememberVariableTarget\(setUserPrompt\)\}/);
});
