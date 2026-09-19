import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const settingsSource = readFileSync(new URL('../src/SettingsView.tsx', import.meta.url), 'utf8');
const apiSource = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');

test('Settings provides independent unpublished add-on management', () => {
  assert.match(settingsSource, /Extras \/ Add-ons/);
  assert.match(settingsSource, /Add new add-on/);
  assert.match(settingsSource, /startEditAddon/);
  assert.match(settingsSource, /itemType: 'addon'/);
  assert.match(settingsSource, /published: false/);
  assert.match(settingsSource, /available with any service/);
});

test('Settings loads the protected full catalogue instead of the public service list', () => {
  assert.match(apiSource, /\/api\/settings\/services/);
  assert.match(settingsSource, /getSettingsServices/);
});
