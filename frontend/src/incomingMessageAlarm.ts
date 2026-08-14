import type { ArrivalSession, ThreadListItem } from './api';

const ENABLED_KEY = 'assistant-ui-incoming-alarm-enabled';
const VOLUME_KEY = 'assistant-ui-incoming-alarm-volume';
const THREAD_SNAPSHOT_KEY = 'assistant-ui-customer-arrival-alarm-snapshot';
const ARRIVAL_SESSION_SNAPSHOT_KEY = 'assistant-ui-arrival-session-alarm-snapshot';
const ARRIVAL_SOUND_ENABLED_KEY = 'assistant-ui-arrival-session-sound-enabled';

export interface IncomingAlarmSettings {
  enabled: boolean;
  volume: number;
}

let audioContext: AudioContext | null = null;
let activeSirens: Array<{ stop: () => void }> = [];

export function getIncomingAlarmSettings(): IncomingAlarmSettings {
  const savedVolume = Number.parseInt(localStorage.getItem(VOLUME_KEY) || '65', 10);
  return {
    enabled: localStorage.getItem(ENABLED_KEY) === 'true',
    volume: Number.isFinite(savedVolume) ? Math.min(100, Math.max(0, savedVolume)) : 65,
  };
}

export function setIncomingAlarmEnabled(enabled: boolean) {
  localStorage.setItem(ENABLED_KEY, String(enabled));
}

export function setIncomingAlarmVolume(volume: number) {
  localStorage.setItem(VOLUME_KEY, String(Math.min(100, Math.max(0, Math.round(volume)))));
}

function getAudioContext(): AudioContext {
  if (!audioContext) {
    const AudioContextConstructor = window.AudioContext
      || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextConstructor) throw new Error('This browser does not support Web Audio.');
    audioContext = new AudioContextConstructor();
  }
  return audioContext;
}

export async function unlockIncomingAlarmAudio() {
  const context = getAudioContext();
  if (context.state === 'suspended') await context.resume();
}

export function getArrivalSoundEnabled(): boolean {
  return localStorage.getItem(ARRIVAL_SOUND_ENABLED_KEY) !== 'false';
}

export function setArrivalSoundEnabled(enabled: boolean) {
  localStorage.setItem(ARRIVAL_SOUND_ENABLED_KEY, String(enabled));
}

export function stopIncomingAlarm() {
  activeSirens.forEach((siren) => siren.stop());
  activeSirens = [];
}

export async function playAirRaidSiren(volume?: number, durationMs = 6500) {
  stopIncomingAlarm();
  const context = getAudioContext();
  if (context.state === 'suspended') await context.resume();

  const selectedVolume = volume ?? getIncomingAlarmSettings().volume;
  const level = Math.min(1, Math.max(0, selectedVolume / 100));
  if (level === 0) return;
  const now = context.currentTime;
  const duration = Math.max(1, durationMs / 1000);
  const master = context.createGain();
  master.gain.setValueAtTime(0.0001, now);
  master.gain.exponentialRampToValueAtTime(Math.max(0.0001, level * 0.22), now + 0.12);
  master.gain.setValueAtTime(Math.max(0.0001, level * 0.22), now + duration - 0.25);
  master.gain.exponentialRampToValueAtTime(0.0001, now + duration);
  master.connect(context.destination);

  const oscillators = [-9, 9].map((detune) => {
    const oscillator = context.createOscillator();
    oscillator.type = 'sawtooth';
    oscillator.detune.value = detune;
    for (let offset = 0; offset < duration; offset += 2.2) {
      const start = now + offset;
      const peak = Math.min(now + duration, start + 1.1);
      const end = Math.min(now + duration, start + 2.2);
      oscillator.frequency.setValueAtTime(410, start);
      oscillator.frequency.exponentialRampToValueAtTime(880, peak);
      oscillator.frequency.exponentialRampToValueAtTime(410, end);
    }
    oscillator.connect(master);
    oscillator.start(now);
    oscillator.stop(now + duration + 0.05);
    return oscillator;
  });

  const siren = {
    stop: () => {
      oscillators.forEach((oscillator) => {
        try { oscillator.stop(); } catch { /* already stopped */ }
        oscillator.disconnect();
      });
      master.disconnect();
    },
  };
  activeSirens = [siren];
  window.setTimeout(() => {
    if (activeSirens.includes(siren)) activeSirens = [];
  }, durationMs + 200);
}

function readPreviousSnapshot(): Record<string, string> | null {
  try {
    const raw = localStorage.getItem(THREAD_SNAPSHOT_KEY);
    return raw ? JSON.parse(raw) as Record<string, string> : null;
  } catch {
    return null;
  }
}

export function processArrivalThreadSnapshot(threads: ThreadListItem[]) {
  const snapshot: Record<string, string> = {};
  threads.forEach((thread) => {
    snapshot[thread.id] = thread.lastArrivalEventId || '';
  });

  const previous = readPreviousSnapshot();
  localStorage.setItem(THREAD_SNAPSHOT_KEY, JSON.stringify(snapshot));
  if (!previous) return;

  const hasNewCustomerArrival = threads.some((thread) => (
    Boolean(thread.lastArrivalEventId)
    && previous[thread.id] !== snapshot[thread.id]
  ));
  if (!hasNewCustomerArrival || !getIncomingAlarmSettings().enabled) return;

  void playAirRaidSiren().catch((error) => {
    console.warn('Customer arrival alarm was blocked by the browser:', error);
  });
}

export function processArrivalSessionSnapshot(sessions: ArrivalSession[]) {
  const snapshot: Record<string, string> = {};
  sessions.forEach((session) => {
    snapshot[session.id] = session.activatedAt || '';
  });

  let previous: Record<string, string> | null = null;
  try {
    const raw = localStorage.getItem(ARRIVAL_SESSION_SNAPSHOT_KEY);
    previous = raw ? JSON.parse(raw) as Record<string, string> : null;
  } catch {
    previous = null;
  }
  localStorage.setItem(ARRIVAL_SESSION_SNAPSHOT_KEY, JSON.stringify(snapshot));
  if (!previous || !getArrivalSoundEnabled()) return;

  const newlyActivated = sessions.some((session) => (
    Boolean(session.activatedAt)
    && previous?.[session.id] !== session.activatedAt
  ));
  if (!newlyActivated) return;

  void playAirRaidSiren().catch((error) => {
    console.warn('Arrival notification sound was blocked by the browser:', error);
  });
}
