import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const settingsSource = readFileSync(new URL('../src/SettingsView.tsx', import.meta.url), 'utf8');
const apiSource = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');

test('Settings exposes a clearly proposal-only manual knowledge curator', () => {
  assert.match(settingsSource, /Knowledge curator/);
  assert.match(settingsSource, /Run knowledge audit/);
  assert.match(settingsSource, /Curator output is never active knowledge/);
  assert.match(settingsSource, /Keep all examples/);
  assert.match(settingsSource, /Create merged draft/);
  assert.match(settingsSource, /Create metadata-repair draft/);
  assert.match(settingsSource, /Add safe replacement draft/);
  assert.match(settingsSource, /Not an issue/);
  assert.match(settingsSource, /Dismiss for now/);
  assert.match(settingsSource, /record_previews/);
  assert.match(settingsSource, /proposal\.actionable !== false/);
  assert.match(settingsSource, /Current record content is intentionally hidden/);
  assert.match(settingsSource, /Reference issue: \{record\.reason_code\}/);
  assert.match(settingsSource, /may use AI credits/);
});

test('curator client uses the protected settings API and explicit state transitions', () => {
  assert.match(apiSource, /\/api\/settings\/knowledge-curator\/run/);
  assert.match(apiSource, /\/api\/settings\/knowledge-curator\/proposals\/\$\{encodeURIComponent\(id\)\}\/resolve/);
  assert.match(apiSource, /KnowledgeCuratorResolution/);
});
