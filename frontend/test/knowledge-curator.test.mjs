import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const settingsSource = readFileSync(new URL('../src/SettingsView.tsx', import.meta.url), 'utf8');
const apiSource = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');

test('Settings exposes a compact, proposal-only owner knowledge workflow', () => {
  assert.match(settingsSource, /Knowledge health:/);
  assert.match(settingsSource, /Check and organise knowledge/);
  assert.match(settingsSource, /Automatic checks create review proposals only/);
  assert.match(settingsSource, /Safe repairs/);
  assert.match(settingsSource, /Business decision/);
  assert.match(settingsSource, /Potentially outdated prices or times/);
  assert.match(settingsSource, /Private or uncertain information/);
  assert.match(settingsSource, /Technical details for support/);
  assert.match(settingsSource, /Use this answer/);
  assert.match(settingsSource, /Keep both because they apply differently/);
  assert.match(settingsSource, /Keep private/);
  assert.match(settingsSource, /Ask me later/);
  assert.match(settingsSource, /create_merged_draft/);
  assert.match(settingsSource, /retrieval-disabled review suggestion|pending review suggestion/);
  assert.doesNotMatch(settingsSource.slice(settingsSource.indexOf('Knowledge health:'), settingsSource.indexOf('Technical details for support')), /record\.id|reason_codes|canonical_key/);
});

test('curator client uses the protected settings API and explicit state transitions', () => {
  assert.match(apiSource, /\/api\/settings\/knowledge-curator\/run/);
  assert.match(apiSource, /\/api\/settings\/knowledge-curator\/proposals\/\$\{encodeURIComponent\(id\)\}\/resolve/);
  assert.match(apiSource, /KnowledgeCuratorResolution/);
  assert.match(apiSource, /safe_repairs_completed/);
  assert.match(apiSource, /interval_seconds/);
  assert.match(apiSource, /'manual' \| 'automatic'/);
});
