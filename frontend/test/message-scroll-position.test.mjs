import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const [mobileInbox, triageDashboard, smsSimulator] = await Promise.all([
  readFile(new URL('../src/MobileInboxView.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../src/SmsTriageDashboard.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../src/SmsClientView.tsx', import.meta.url), 'utf8'),
]);

test('message threads scroll to the latest message once per open thread', () => {
  assert.match(mobileInbox, /initiallyScrolledThreadRef\.current === selectedId/);
  assert.match(mobileInbox, /viewport\.scrollTop = viewport\.scrollHeight/);
  assert.match(mobileInbox, /if \(!selectedId\) \{[\s\S]*initiallyScrolledThreadRef\.current = null/);

  assert.match(triageDashboard, /initiallyScrolledThreadRef\.current === threadId/);
  assert.match(triageDashboard, /viewport\.scrollTop = viewport\.scrollHeight/);

  // Incoming messages and run/thread updates must not opt back into automatic scrolling.
  assert.doesNotMatch(smsSimulator, /scrollIntoView\s*\(/);
  assert.doesNotMatch(smsSimulator, /\.scrollTo\s*\(/);
  assert.doesNotMatch(smsSimulator, /\.scrollTop\s*=/);

  assert.match(triageDashboard, /<ThreadPrimitive\.Viewport[\s\S]*?autoScroll=\{false\}/);
  assert.match(triageDashboard, /<ThreadPrimitive\.Viewport[\s\S]*?scrollToBottomOnInitialize=\{false\}/);
  assert.match(triageDashboard, /<ThreadPrimitive\.Viewport[\s\S]*?scrollToBottomOnRunStart=\{false\}/);
  assert.match(triageDashboard, /<ThreadPrimitive\.Viewport[\s\S]*?scrollToBottomOnThreadSwitch=\{false\}/);
});

test('rapid message scrolling cannot dismiss the open mobile thread', () => {
  assert.doesNotMatch(mobileInbox, /onTouchStart=/);
  assert.doesNotMatch(mobileInbox, /onTouchEnd=/);
  assert.match(mobileInbox, /aria-label="Back to conversations"/);
});
