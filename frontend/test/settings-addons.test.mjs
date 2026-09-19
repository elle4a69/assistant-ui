import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const settingsSource = readFileSync(new URL('../src/SettingsView.tsx', import.meta.url), 'utf8');
const apiSource = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');

test('Settings manages shared non-bookable service add-ons', () => {
  assert.match(settingsSource, /Service Add-ons &amp; Extras/);
  assert.match(settingsSource, /Shared across Line 1 and Line 2/);
  assert.match(settingsSource, /cannot be booked as standalone services/);
  assert.match(settingsSource, /Extra price/);
  assert.match(settingsSource, /Extra time/);
  assert.match(settingsSource, /handleUpdateServiceAddon/);
});

test('add-ons use their own protected persistence API', () => {
  assert.match(apiSource, /\/api\/settings\/service-addons/);
  assert.match(apiSource, /JSON\.stringify\(\{ addons \}\)/);
  assert.match(settingsSource, /getServiceAddons/);
  assert.match(settingsSource, /saveServiceAddons\(serviceAddons\)/);
});
