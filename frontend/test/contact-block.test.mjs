import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const apiSource = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8');
const inboxSource = await readFile(new URL('../src/MobileInboxView.tsx', import.meta.url), 'utf8');

test('contact block API is thread scoped and exposes persisted state', () => {
  assert.match(apiSource, /contactBlocked: boolean/);
  assert.match(apiSource, /api\/threads\/\$\{threadId\}\/contact-block/);
  assert.match(apiSource, /body: JSON\.stringify\(\{ blocked \}\)/);
  assert.match(apiSource, /payload\?\.detail \|\| 'Failed to change contact block status'/);
});

test('message footer shows a right-aligned reversible block control opposite AI replies', () => {
  const footerStart = inboxSource.indexOf('<form onSubmit={sendMessage}');
  const footerEnd = inboxSource.indexOf('</form>', footerStart);
  const footer = inboxSource.slice(footerStart, footerEnd);

  assert.match(footer, /AI replies/);
  assert.match(footer, /ml-auto flex items-center/);
  assert.match(footer, /Unblock contact/);
  assert.match(footer, /Block contact/);
  assert.match(footer, /role="switch"/);
  assert.match(footer, /aria-checked=\{thread\?\.contactBlocked \?\? false\}/);
  assert.match(inboxSource, /window\.confirm\(`Block \$\{contactLabel\(thread\.customerPhone\)\}/);
  assert.match(inboxSource, /Automated handling and replies will stop for this contact on this SMS line/);
  assert.match(inboxSource, /setContactBlocked\(threadId, blocked\)/);
});
