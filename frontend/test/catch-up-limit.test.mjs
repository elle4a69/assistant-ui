import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const mobileInboxSource = await readFile(
  new URL('../src/MobileInboxView.tsx', import.meta.url),
  'utf8',
);

test('manual catch-up delegates one bounded batch to the server', () => {
  assert.equal((mobileInboxSource.match(/await catchUpMissedMessage\(\)/g) || []).length, 1);
  assert.doesNotMatch(mobileInboxSource, /safetyLimit/);
});
