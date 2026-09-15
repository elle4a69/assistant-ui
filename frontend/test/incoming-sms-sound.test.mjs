import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

class FakeAudioContext {
  state = 'suspended';
  currentTime = 10;
  destination = {};
  resumeCalls = 0;
  oscillators = [];
  actions = [];

  async resume() {
    this.actions.push('resume');
    this.resumeCalls += 1;
    if (!this.keepSuspended) this.state = 'running';
  }

  createGain() {
    return {
      gain: {
        setValueAtTime() {},
        linearRampToValueAtTime() {},
        exponentialRampToValueAtTime() {},
      },
      connect() {},
      disconnect() {},
    };
  }

  createOscillator() {
    const oscillator = {
      type: 'sine',
      frequency: { setValueAtTime() {} },
      connect() {},
      disconnect() {},
      start: () => { oscillator.started = true; this.actions.push('start'); },
      stop: () => { oscillator.stopped = true; },
      addEventListener() {},
      started: false,
      stopped: false,
    };
    this.oscillators.push(oscillator);
    return oscillator;
  }
}

const audioContext = new FakeAudioContext();
globalThis.window = { AudioContext: class { constructor() { return audioContext; } } };

const source = await readFile(new URL('../src/incomingMessageAlarm.ts', import.meta.url), 'utf8');
const settingsSource = await readFile(new URL('../src/SettingsView.tsx', import.meta.url), 'utf8');
const appSource = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`;
const alarm = await import(moduleUrl);

function thread(id, overrides = {}) {
  return {
    id,
    lastMessageAt: '2026-09-15T01:00:00Z',
    lastMessageText: 'Hello',
    lastMessageRole: 'customer',
    ...overrides,
  };
}

test('the first SMS snapshot is only a baseline', () => {
  const result = alarm.processIncomingSmsSnapshot([thread('existing')], null);
  assert.equal(result.hasNewInboundMessage, false);
});

test('a new inbound SMS is reported once and polling duplicates stay silent', () => {
  const baseline = alarm.processIncomingSmsSnapshot([
    thread('existing', { lastMessageRole: 'agent' }),
  ], null);
  const inbound = thread('existing', {
    lastMessageAt: '2026-09-15T01:01:00Z',
    lastMessageText: 'New reply',
  });
  const arrived = alarm.processIncomingSmsSnapshot([inbound], baseline.snapshot);
  assert.equal(arrived.hasNewInboundMessage, true);
  assert.equal(alarm.processIncomingSmsSnapshot([inbound], arrived.snapshot).hasNewInboundMessage, false);
});

test('outbound updates stay silent and missing threads retain their seen marker', () => {
  const baseline = alarm.processIncomingSmsSnapshot([thread('existing')], null);
  const missing = alarm.processIncomingSmsSnapshot([], baseline.snapshot);
  const returned = alarm.processIncomingSmsSnapshot([thread('existing')], missing.snapshot);
  assert.equal(returned.hasNewInboundMessage, false);

  const outbound = alarm.processIncomingSmsSnapshot([
    thread('existing', { lastMessageAt: '2026-09-15T01:02:00Z', lastMessageRole: 'agent' }),
  ], returned.snapshot);
  assert.equal(outbound.hasNewInboundMessage, false);
});

test('a new customer thread appearing after baseline is inbound', () => {
  const baseline = alarm.processIncomingSmsSnapshot([], null);
  assert.equal(
    alarm.processIncomingSmsSnapshot([thread('new-thread')], baseline.snapshot).hasNewInboundMessage,
    true,
  );
});

test('audio starts blocked and cannot be unlocked without a trusted gesture', async () => {
  assert.equal(alarm.getIncomingAlarmAudioState(), 'blocked');
  await assert.rejects(
    alarm.unlockIncomingAlarmAudio({ isTrusted: false }),
    /real tap or key press/,
  );
  assert.equal(audioContext.resumeCalls, 0);
  assert.equal(alarm.getIncomingAlarmAudioState(), 'blocked');
});

test('a user gesture resumes and primes browser audio for later inbound alerts', async () => {
  await alarm.unlockIncomingAlarmAudio({ isTrusted: true });
  assert.equal(audioContext.state, 'running');
  assert.equal(alarm.getIncomingAlarmAudioState(), 'enabled');
  assert.equal(audioContext.resumeCalls, 1);
  assert.equal(audioContext.oscillators.length, 1);
  assert.equal(audioContext.oscillators[0].started, true);
  assert.equal(audioContext.oscillators[0].stopped, true);
  assert.deepEqual(audioContext.actions.slice(0, 2), ['start', 'resume']);

  await alarm.playIncomingMessageSound();
  assert.equal(audioContext.oscillators.length, 2);
  assert.equal(audioContext.oscillators[1].started, true);
});

test('audio unlock reports browsers that resolve resume without allowing playback', async () => {
  audioContext.state = 'suspended';
  audioContext.keepSuspended = true;
  await assert.rejects(
    alarm.unlockIncomingAlarmAudio({ isTrusted: true }),
    /has not allowed sound/,
  );
  audioContext.keepSuspended = false;
});

test('a blocked inbound alert retries once after unlock and is not duplicated', async () => {
  let playCalls = 0;
  let blocked = true;
  const player = alarm.createIncomingMessageSoundPlayer(async () => {
    playCalls += 1;
    if (blocked) throw new Error('gesture required');
  });

  await assert.rejects(player.notify(), /gesture required/);
  assert.equal(player.hasPending(), true);
  blocked = false;
  await player.retry();
  await player.retry();

  assert.equal(playCalls, 2);
  assert.equal(player.hasPending(), false);
});

test('settings distinguish the saved preference from gesture-blocked playback', () => {
  assert.match(settingsSource, /incomingMessageAudioState !== 'enabled'/);
  assert.match(settingsSource, /this device needs a tap to enable playback/);
  assert.match(settingsSource, /role="status"/);
  assert.match(settingsSource, /aria-describedby=\{incomingMessageSoundEnabled/);
  assert.doesNotMatch(appSource, /sound permission|permission was granted/i);
  assert.doesNotMatch(appSource, /addEventListener\('pointerdown', unlockAudio/);
});
