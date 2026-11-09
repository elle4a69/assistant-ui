import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const apiSource = await readFile(
  new URL('../src/api.ts', import.meta.url),
  'utf8',
);

const settingsViewSource = await readFile(
  new URL('../src/SettingsView.tsx', import.meta.url),
  'utf8',
);

test('api.ts includes publicWebpageEnabled in SystemSettings and updateSettings', () => {
  assert.match(apiSource, /publicWebpageEnabled\?: boolean;/);
  assert.match(apiSource, /updateSettings\([^)]*publicWebpageEnabled\?: boolean/);
});

test('SettingsView includes Public Webpage toggle and handler', () => {
  assert.match(settingsViewSource, /const \[publicWebpageEnabled, setPublicWebpageEnabled\] = useState\(true\);/);
  assert.match(settingsViewSource, /handlePublicWebpageToggle/);
  assert.match(settingsViewSource, /Public Webpage/);
  assert.match(settingsViewSource, /Enable or disable the public landing and booking webpage/);
  assert.match(settingsViewSource, /updateSettings\(\{\s*publicWebpageEnabled:\s*nextValue\s*\}\)/);
});
