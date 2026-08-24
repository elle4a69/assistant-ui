import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const mobileInboxSource = await readFile(
  new URL('../src/MobileInboxView.tsx', import.meta.url),
  'utf8',
);

test('manual catch-up processes no more than 50 conversations per run', () => {
  assert.match(mobileInboxSource, /const safetyLimit = 50\b/);
  assert.doesNotMatch(mobileInboxSource, /const safetyLimit = 250\b/);
});
