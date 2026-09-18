import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const [mobileInbox, triageDashboard, smsSimulator] = await Promise.all([
  readFile(new URL('../src/MobileInboxView.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../src/SmsTriageDashboard.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../src/SmsClientView.tsx', import.meta.url), 'utf8'),
]);

test('opening a mobile SMS conversation starts at the latest message', () => {
  assert.match(mobileInbox, /ref=\{messageViewportRef\}/);
  assert.match(mobileInbox, /initiallyScrolledThreadRef/);
  assert.match(mobileInbox, /viewport\.scrollTop\s*=\s*viewport\.scrollHeight/);
});

test('other message views still do not force the viewport to the latest message', () => {
  for (const source of [triageDashboard, smsSimulator]) {
    assert.doesNotMatch(source, /scrollIntoView\s*\(/);
    assert.doesNotMatch(source, /\.scrollTo\s*\(/);
    assert.doesNotMatch(source, /\.scrollTop\s*=/);
  }

  assert.match(triageDashboard, /<ThreadPrimitive\.Viewport[\s\S]*?autoScroll=\{false\}/);
  assert.match(triageDashboard, /<ThreadPrimitive\.Viewport[\s\S]*?scrollToBottomOnInitialize=\{false\}/);
  assert.match(triageDashboard, /<ThreadPrimitive\.Viewport[\s\S]*?scrollToBottomOnRunStart=\{false\}/);
  assert.match(triageDashboard, /<ThreadPrimitive\.Viewport[\s\S]*?scrollToBottomOnThreadSwitch=\{false\}/);
});
