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
  assert.match(inbox, /aria-label="Conversation controls"/);
  assert.match(inbox, /thread\.pinned \? 'Unpin' : 'Pin'/);
  assert.match(inbox, /thread\.blocked \? 'Unblock' : 'Block'/);
  assert.match(inbox, /changingPinned \? \(thread\.pinned \? 'Unpinning…' : 'Pinning…'\)/);
  assert.match(inbox, /changingBlocked \? \(thread\.blocked \? 'Unblocking…' : 'Blocking…'\)/);
  assert.match(inbox, /aria-label=\{changingPinned .*'Unpin conversation'.*'Pin conversation'\}/);
  assert.match(inbox, /aria-label=\{changingBlocked .*'Unblock contact'.*'Block contact'\}/);
  assert.match(api, /\/api\/threads\/\$\{threadId\}\/pin/);
  assert.match(api, /\/api\/threads\/\$\{threadId\}\/block/);
});

test('Settings lists blocked callers by SMS account and can unblock them', () => {
  assert.match(settings, />Blocked contacts \/ numbers</);
  assert.match(settings, /id="blocked-contacts"/);
  assert.match(settings, /Loading blocked contacts…/);
  assert.match(settings, /Blocked contacts could not be loaded/);
  assert.match(settings, /onClick=\{loadBlockedContacts\}/);
  assert.match(settings, /contact\.smsAccountKey === 'secondary' \? 'SMS Line 2' : 'SMS Line 1'/);
  assert.match(settings, /unblockContact\(contact\.smsAccountKey, contact\.customerPhone\)/);
  assert.match(settings, /aria-label=\{`Unblock \$\{contact\.customerPhone\}/);
  assert.match(api, /\/api\/settings\/blocked-contacts\?\$\{query\}/);
});
