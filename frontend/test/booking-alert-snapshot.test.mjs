import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const sourceUrl = new URL('../src/incomingMessageAlarm.ts', import.meta.url);
const source = await readFile(sourceUrl, 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`;

class MemoryStorage {
  values = new Map();

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.values.set(key, String(value));
  }
}

function booking(id, startTime, overrides = {}) {
  return {
    id,
    customerPhone: '+61400000000',
    summary: 'Customer - Service (Tori)',
    startTime,
    endTime: new Date(Date.parse(startTime) + 30 * 60 * 1000).toISOString(),
    status: 'scheduled',
    ...overrides,
  };
}

async function loadAlarmModule() {
  globalThis.localStorage = new MemoryStorage();
  return import(`${moduleUrl}#${Math.random()}`);
}

test('a booking that temporarily disappears never alerts again when it returns', async () => {
  const alarm = await loadAlarmModule();
  const originalNow = Date.now;
  Date.now = () => Date.parse('2026-08-24T00:00:00Z');
  try {
    const existing = booking('existing', '2026-08-25T03:00:00Z');
    assert.deepEqual(alarm.processBookingSnapshot([existing]), []);
    assert.deepEqual(alarm.processBookingSnapshot([]), []);
    assert.deepEqual(alarm.processBookingSnapshot([existing]), []);

    const genuinelyNew = booking('new', '2026-08-26T03:00:00Z');
    assert.deepEqual(alarm.processBookingSnapshot([existing, genuinelyNew]), [genuinelyNew]);
    assert.deepEqual(alarm.processBookingSnapshot([genuinelyNew]), []);
  } finally {
    Date.now = originalNow;
  }
});

test('historical, cancelled and mirrored duplicate bookings do not create repeated alerts', async () => {
  const alarm = await loadAlarmModule();
  const originalNow = Date.now;
  Date.now = () => Date.parse('2026-08-24T00:00:00Z');
  try {
    assert.deepEqual(alarm.processBookingSnapshot([]), []);

    const past = booking('past', '2026-08-20T03:00:00Z');
    const cancelled = booking('cancelled', '2026-08-26T03:00:00Z', { status: 'cancelled' });
    const firstCopy = booking('google-id', '2026-08-27T03:00:00Z');
    const mirroredCopy = booking('sqlite-id', '2026-08-27T03:00:00Z');
    assert.deepEqual(
      alarm.processBookingSnapshot([past, cancelled, firstCopy, mirroredCopy]),
      [firstCopy],
    );
    assert.deepEqual(alarm.processBookingSnapshot([past, cancelled, mirroredCopy]), []);
  } finally {
    Date.now = originalNow;
  }
});

test('an existing booking edit keeps the original booking identity acknowledged', async () => {
  const alarm = await loadAlarmModule();
  const originalNow = Date.now;
  Date.now = () => Date.parse('2026-08-24T00:00:00Z');
  try {
    const existing = booking('same-id', '2026-08-25T03:00:00Z');
    assert.deepEqual(alarm.processBookingSnapshot([existing]), []);

    const edited = booking('same-id', '2026-08-28T03:00:00Z', { summary: 'Updated service' });
    assert.deepEqual(alarm.processBookingSnapshot([edited]), []);
  } finally {
    Date.now = originalNow;
  }
});
