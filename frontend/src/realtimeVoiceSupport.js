export function getRealtimeVoiceUnavailableReason({
  isSecureContext,
  hasGetUserMedia,
  hasPeerConnection,
}) {
  if (!isSecureContext) {
    return 'Realtime voice requires a secure HTTPS connection. Open this page over HTTPS and try again.';
  }
  if (!hasGetUserMedia) {
    return 'This browser cannot access a microphone for realtime voice. Use a current version of Chrome, Edge, or Safari and allow microphone access.';
  }
  if (!hasPeerConnection) {
    return 'This browser does not support realtime voice connections. Use a current version of Chrome, Edge, or Safari.';
  }
  return null;
}

export function describeRealtimeVoiceError(error) {
  const name = error && typeof error === 'object' && 'name' in error ? String(error.name) : '';
  const message = error instanceof Error ? error.message.trim() : '';

  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Microphone access was denied. Allow microphone access for this site in your browser settings, then try again.';
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return 'No microphone was found. Connect or enable a microphone, then try again.';
  }
  if (name === 'NotReadableError' || name === 'TrackStartError' || name === 'AbortError') {
    return 'The microphone is unavailable or already in use. Close other apps using it, check the device, then try again.';
  }
  if (message) return message;
  return 'Realtime voice could not start. Check your microphone and connection, then try again.';
}
