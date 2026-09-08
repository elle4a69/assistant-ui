import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const settingsSource = readFileSync(new URL('../src/SettingsView.tsx', import.meta.url), 'utf8');
const apiSource = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');

test('Settings exposes a conversational, proposal-only owner knowledge workflow', () => {
  assert.match(settingsSource, /Knowledge health:/);
  assert.match(settingsSource, /Check and organise knowledge/);
  assert.match(settingsSource, /Safe repairs/);
  assert.match(settingsSource, /Start review/);
  assert.match(settingsSource, /Question \{/);
  assert.match(settingsSource, /Saved information/);
  assert.match(settingsSource, /Or type your answer/);
  assert.match(settingsSource, /Yes, save this/);
  assert.match(settingsSource, /Skip for now/);
  assert.match(settingsSource, /Review complete/);
  assert.match(settingsSource, /Technical details for support/);
  assert.match(settingsSource, /Learning review queue/);
  const ownerView = settingsSource.slice(settingsSource.indexOf('Knowledge health:'), settingsSource.indexOf('Technical details for support'));
  assert.doesNotMatch(ownerView, /record\.id|reason_codes|canonical_key|Business decision/);
  assert.doesNotMatch(ownerView, /Edit the answer|Ask me later/);
});

test('curator client uses protected interview endpoints and explicit confirmation', () => {
  assert.match(apiSource, /\/api\/settings\/knowledge-curator\/run/);
  assert.match(apiSource, /\/api\/settings\/knowledge-curator\/interview\/start/);
  assert.match(apiSource, /\/api\/settings\/knowledge-curator\/interview\/answer/);
  assert.match(apiSource, /\/api\/settings\/knowledge-curator\/interview\/confirm/);
  assert.match(apiSource, /safe_repairs_completed/);
});
