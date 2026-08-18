import assert from 'node:assert/strict';
import test from 'node:test';

import {
  describeRealtimeVoiceError,
  getRealtimeVoiceUnavailableReason,
} from '../src/realtimeVoiceSupport.js';

test('voice control is available when the secure browser supports microphone and WebRTC', () => {
  assert.equal(getRealtimeVoiceUnavailableReason({
    isSecureContext: true,
    hasGetUserMedia: true,
    hasPeerConnection: true,
  }), null);
});

test('unsupported voice environments produce actionable guidance', () => {
  assert.match(getRealtimeVoiceUnavailableReason({
    isSecureContext: false,
    hasGetUserMedia: true,
    hasPeerConnection: true,
  }), /HTTPS/);
  assert.match(getRealtimeVoiceUnavailableReason({
    isSecureContext: true,
    hasGetUserMedia: false,
    hasPeerConnection: true,
  }), /browser cannot access a microphone/);
  assert.match(getRealtimeVoiceUnavailableReason({
    isSecureContext: true,
    hasGetUserMedia: true,
    hasPeerConnection: false,
  }), /browser does not support realtime voice/);
});

test('denied and unavailable microphones produce actionable guidance', () => {
  assert.match(describeRealtimeVoiceError(Object.assign(new Error(), { name: 'NotAllowedError' })), /browser settings/);
  assert.match(describeRealtimeVoiceError(Object.assign(new Error(), { name: 'NotFoundError' })), /Connect or enable/);
  assert.match(describeRealtimeVoiceError(Object.assign(new Error(), { name: 'NotReadableError' })), /other apps/);
});

test('voice service errors remain visible instead of being replaced by a generic failure', () => {
  assert.equal(
    describeRealtimeVoiceError(new Error('Realtime voice is unavailable because the service is not configured.')),
    'Realtime voice is unavailable because the service is not configured.',
  );
});
