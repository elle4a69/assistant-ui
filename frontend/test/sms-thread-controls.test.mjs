import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const inbox = await readFile(new URL('../src/MobileInboxView.tsx', import.meta.url), 'utf8');
const app = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8');
const styles = await readFile(new URL('../src/index.css', import.meta.url), 'utf8');
const settings = await readFile(new URL('../src/SettingsView.tsx', import.meta.url), 'utf8');
const api = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8');

test('SMS thread controls expose persisted pin and confirmed account-scoped blocking', () => {
  assert.match(inbox, /window\.confirm\(`Block/);
  assert.match(inbox, /setThreadPinned\(thread\.id, pinned\)/);
  assert.match(inbox, /setThreadBlocked\(thread\.id, blocked\)/);
  assert.match(inbox, /grid grid-cols-5 gap-1/);
  assert.match(inbox, /aria-label=\{thread\?\.pinned \? 'Unpin conversation' : 'Pin conversation'\}/);
  assert.match(inbox, /changingPinned \? '…' : thread\?\.pinned \? 'Unpin' : 'Pin'/);
  assert.match(inbox, /aria-label=\{thread\?\.blocked \? 'Unblock contact' : 'Block contact'\}/);
  assert.match(inbox, /changingBlocked \? '…' : thread\?\.blocked \? 'Unblock' : 'Block'/);
  assert.match(inbox, /aria-pressed=\{aiEnabled\}/);
  assert.match(inbox, /aiEnabled \? 'AI On' : 'AI Off'/);
  assert.match(inbox, /aria-pressed=\{trainingEnabled\}/);
  assert.match(inbox, /trainingEnabled \? 'Train On' : 'Train Off'/);
  assert.doesNotMatch(inbox, /role="switch"/);
  assert.match(api, /\/api\/threads\/\$\{threadId\}\/pin/);
  assert.match(api, /\/api\/threads\/\$\{threadId\}\/block/);
});

test('mobile composer grows above controls and the app nav occupies the bottom row', () => {
  const composerPosition = inbox.indexOf('data-testid="message-composer"');
  const controlsPosition = inbox.indexOf('aria-label="Conversation controls"');
  assert.ok(composerPosition >= 0);
  assert.ok(controlsPosition > composerPosition);
  assert.match(inbox, /useLayoutEffect\(\(\) => \{[\s\S]*textarea\.scrollHeight[\s\S]*\}, \[composer\]\)/);
  assert.match(inbox, /maximumHeight = 144/);
  assert.match(app, /data-testid="mobile-bottom-nav"/);
  assert.match(app, /pb-16 sm:pb-0/);
});

test('installed PWA expands the body and moves the complete menu to its new bottom', () => {
  assert.match(app, /isEmbeddedBooking \? 'min-h-0 overflow-visible' : 'portal-app-shell fixed left-0 right-0 top-0 overflow-hidden'/);
  assert.match(app, /window\.screen\.height - window\.innerHeight/);
  assert.match(app, /measuredGap > 0 && measuredGap <= IOS_STANDALONE_BOTTOM_GAP_LIMIT/);
  assert.match(app, /data-testid="mobile-bottom-nav" className="absolute bottom-0 left-0 right-0 z-\[110\]/);
  assert.match(app, /className="relative z-\[1\] flex h-full w-full/);
  assert.match(app, /className=\{`relative z-\[2\] flex flex-col/);
  assert.doesNotMatch(app, /mobile-bottom-nav[^\n]+safe-area-inset-bottom/);
  assert.match(app, /className="relative z-\[1\] flex h-full w-full items-center justify-around px-1 overflow-x-auto/);
  assert.match(styles, /\.portal-app-shell \{[\s\S]*height: calc\(100% \+ var\(--ios-standalone-bottom-gap, 0px\)\);/);
  assert.match(app, /\[scrollbar-width:none\] \[-ms-overflow-style:none\] \[&::\-webkit-scrollbar\]:hidden/);
  assert.match(styles, /html, body, #root \{[\s\S]*background-color: #0f172a/);
});

test('authenticated internal booking form restores the app menu without exposing it in public embeds', () => {
  assert.match(app, /const isBookingRoute =[^\n]+window\.location\.pathname === '\/booking'/);
  assert.match(app, /const \[bookingAdminAuthenticated, setBookingAdminAuthenticated\] = useState\(false\)/);
  assert.match(app, /getAdminAuthStatus\(\)[\s\S]*setBookingAdminAuthenticated\(result\.authenticated\)/);
  assert.match(app, /isStandaloneBooking = isEmbeddedBooking \|\| \(isBookingRoute && !bookingAdminAuthenticated\)/);
  assert.match(app, /window\.location\.pathname\.startsWith\('\/v2'\)/);
});

test('Return inserts a new line and sending requires the Send button', () => {
  assert.match(inbox, /enterKeyHint="enter"/);
  assert.doesNotMatch(inbox, /requestSubmit\(\)/);
});

test('Settings lists blocked callers by SMS account and can unblock them', () => {
  assert.match(settings, /href="#blocked-contacts"/);
  assert.match(settings, />Blocked contacts &amp; numbers</);
  assert.match(settings, /id="blocked-contacts" open/);
  assert.match(settings, />No blocked contacts or numbers</);
  assert.match(settings, /contact\.smsAccountKey === 'secondary' \? 'SMS Line 2' : 'SMS Line 1'/);
  assert.match(settings, /unblockContact\(contact\.smsAccountKey, contact\.customerPhone\)/);
  assert.match(api, /\/api\/settings\/blocked-contacts\?\$\{query\}/);
});
