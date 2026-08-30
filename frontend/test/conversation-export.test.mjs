import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const apiSource = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');
const settingsSource = readFileSync(new URL('../src/SettingsView.tsx', import.meta.url), 'utf8');

test('conversation export uses the authenticated settings API and an explicit line scope', () => {
  const exportClient = apiSource.slice(
    apiSource.indexOf('export async function downloadConversationCsv'),
    apiSource.indexOf('export interface BusinessVariable'),
  );

  assert.match(exportClient, /\/api\/settings\/conversations\/export\.csv/);
  assert.match(exportClient, /encodeURIComponent\(smsAccountKey\)/);
  assert.match(exportClient, /response\.blob\(\)/);
  assert.match(exportClient, /content-disposition/);
});

test('Settings offers CSV download controls for all or individual SMS lines', () => {
  assert.match(settingsSource, /Conversation Data Export/);
  assert.match(settingsSource, /Download conversation CSV/);
  assert.match(settingsSource, /All permitted lines/);
  assert.match(settingsSource, /value="primary"/);
  assert.match(settingsSource, /value="secondary"/);
  assert.match(settingsSource, /downloadConversationCsv\(conversationExportScope\)/);
});
