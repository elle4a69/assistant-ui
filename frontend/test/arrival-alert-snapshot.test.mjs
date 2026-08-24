import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const sourceUrl = new URL('../src/incomingMessageAlarm.ts', import.meta.url);
const source = await readFile(sourceUrl, 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`;

class MemoryStorage {
  values = new Map();
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, String(value)); }
}

function arrival(overrides = {}) {
  return {
    id: 'arrival-session',
    threadId: 'secondary-thread',
    smsAccountKey: 'secondary',
    status: 'active',
    activatedAt: '2026-08-24T02:00:00Z',
    acknowledgedAt: null,
    lastAlertAt: '2026-08-24T02:00:00Z',
    ...overrides,
  };
}

async function loadAlarmModule() {
  globalThis.localStorage = new MemoryStorage();
  globalThis.localStorage.setItem('assistant-ui-incoming-alarm-enabled', 'false');
  return import(`${moduleUrl}#${Math.random()}`);
}

test('arrival alert repeats only when the server advances its sixty-second alert cycle', async () => {
  const alarm = await loadAlarmModule();
  const first = arrival();
  assert.deepEqual(alarm.processArrivalSessionSnapshot([first]), [first]);
  assert.deepEqual(alarm.processArrivalSessionSnapshot([first]), []);

  const repeated = arrival({ lastAlertAt: '2026-08-24T02:01:00Z', alertCount: 2 });
  assert.deepEqual(alarm.processArrivalSessionSnapshot([repeated]), [repeated]);
  assert.deepEqual(alarm.processArrivalSessionSnapshot([repeated]), []);
});

test('acknowledgement removes the arrival from all later alert snapshots', async () => {
  const alarm = await loadAlarmModule();
  const pending = arrival();
  assert.deepEqual(alarm.processArrivalSessionSnapshot([pending]), [pending]);

  const acknowledged = arrival({ acknowledgedAt: '2026-08-24T02:00:20Z' });
  assert.deepEqual(alarm.processArrivalSessionSnapshot([acknowledged]), []);
  assert.deepEqual(
    alarm.processArrivalSessionSnapshot([arrival({ lastAlertAt: '2026-08-24T02:01:00Z', acknowledgedAt: '2026-08-24T02:00:20Z' })]),
    [],
  );
});

test('simultaneous arrivals stay queued and acknowledgement advances to the next customer', async () => {
  const alarm = await loadAlarmModule();
  const first = arrival({ id: 'arrival-one' });
  const second = arrival({ id: 'arrival-two', threadId: 'primary-thread' });
  const queued = alarm.mergeArrivalAlertQueue([], [first, second], [first, second]);
  assert.deepEqual(queued.map(item => item.id), ['arrival-one', 'arrival-two']);

  const acknowledgedFirst = { ...first, acknowledgedAt: '2026-08-24T02:00:20Z' };
  const advanced = alarm.mergeArrivalAlertQueue(queued, [acknowledgedFirst, second], []);
  assert.deepEqual(advanced.map(item => item.id), ['arrival-two']);
});
