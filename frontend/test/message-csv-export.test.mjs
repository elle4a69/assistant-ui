import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const apiSource = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');
const settingsSource = readFileSync(new URL('../src/SettingsView.tsx', import.meta.url), 'utf8');

test('settings message export uses the authenticated CSV endpoint', () => {
  const exportClient = apiSource.slice(
    apiSource.indexOf('export async function downloadMessagesCsv'),
    apiSource.indexOf('export async function updateSettings'),
  );

  assert.match(exportClient, /apiFetch\(`\$\{API_BASE\}\/api\/settings\/messages\/export\.csv/);
  assert.match(exportClient, /credentials: 'same-origin'/);
  assert.match(exportClient, /content-disposition/);
  assert.match(exportClient, /response\.blob\(\)/);
});

test('settings exposes export progress and clear success and error feedback', () => {
  assert.match(settingsSource, />Export messages</);
  assert.match(settingsSource, /disabled=\{exportingMessages\}/);
  assert.match(settingsSource, /await downloadMessagesCsv\(\)/);
  assert.match(settingsSource, /Message CSV export downloaded/);
  assert.match(settingsSource, /Failed to export messages/);
});
