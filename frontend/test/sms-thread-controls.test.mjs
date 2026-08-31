import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const inbox = await readFile(new URL('../src/MobileInboxView.tsx', import.meta.url), 'utf8');
const settings = await readFile(new URL('../src/SettingsView.tsx', import.meta.url), 'utf8');
const api = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8');

test('SMS thread controls expose persisted pin and confirmed account-scoped blocking', () => {
  assert.match(inbox, /window\.confirm\(`Block/);
  assert.match(inbox, /setThreadPinned\(thread\.id, pinned\)/);
  assert.match(inbox, /setThreadBlocked\(thread\.id, blocked\)/);
  assert.match(inbox, /aria-label=\{thread\.pinned \? 'Unpin conversation' : 'Pin conversation'\}/);
  assert.match(api, /\/api\/threads\/\$\{threadId\}\/pin/);
  assert.match(api, /\/api\/threads\/\$\{threadId\}\/block/);
});

test('Settings lists blocked callers by SMS account and can unblock them', () => {
  assert.match(settings, />Blocked callers</);
  assert.match(settings, /contact\.smsAccountKey === 'secondary' \? 'SMS Line 2' : 'SMS Line 1'/);
  assert.match(settings, /unblockContact\(contact\.smsAccountKey, contact\.customerPhone\)/);
  assert.match(api, /\/api\/settings\/blocked-contacts\?\$\{query\}/);
});
