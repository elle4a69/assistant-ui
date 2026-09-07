import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const dashboard = await readFile(
  new URL('../src/SmsTriageDashboard.tsx', import.meta.url),
  'utf8',
);

test('thread timeline exposes structured booking diagnostics in a compact expansion', () => {
  for (const eventType of [
    'availability_lookup_started',
    'availability_lookup_completed',
    'availability_lookup_failed',
    'booking_decision',
    'booking_attempted',
    'booking_succeeded',
    'booking_conflict',
    'booking_failed',
  ]) {
    assert.match(dashboard, new RegExp(`'${eventType}'`));
  }
  assert.match(dashboard, /<details key=/);
  assert.match(dashboard, />Correlation</);
  assert.match(dashboard, />Freshness</);
  assert.match(dashboard, />Candidates</);
  assert.match(dashboard, />Pending</);
  assert.match(dashboard, /generated_reply_message_id/);
});
