import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createOperationsRealtimeSession,
  persistOperationsRealtimeTurn,
  runOperationsRealtimeTool,
} from './api';
import {
  pairReadyRealtimeTranscripts,
  parseCompletedRealtimeTranscript,
  parseRealtimeAssistantFailure,
  parseRealtimeUserTranscriptState,
  upsertRealtimeAssistantTranscript,
  upsertRealtimeUserTranscript,
  type RealtimeAssistantTranscriptState,
  type RealtimeUserTranscriptState,
} from './operationsRealtimeVoiceProtocol';

export type OperationsRealtimeVoiceState = 'idle' | 'connecting' | 'live';

interface OperationsRealtimeVoiceOptions {
  onTurnPersisted?: () => void;
  onError?: (message: string) => void;
}

function realtimeSessionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (character) => {
    const random = Math.floor(Math.random() * 16);
    const value = character === 'x' ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export function useOperationsRealtimeVoice(options: OperationsRealtimeVoiceOptions = {}) {
  const [voiceState, setVoiceState] = useState<OperationsRealtimeVoiceState>('idle');
  const voiceStateRef = useRef<OperationsRealtimeVoiceState>('idle');
  const peerConnectionRef = useRef<RTCPeerConnection | null>(null);
  const microphoneStreamRef = useRef<MediaStream | null>(null);
  const audioElementRef = useRef<HTMLAudioElement | null>(null);
  const dataChannelRef = useRef<RTCDataChannel | null>(null);
  const connectionGenerationRef = useRef(0);
  const sessionIdRef = useRef('');
  const usersRef = useRef<RealtimeUserTranscriptState[]>([]);
  const assistantsRef = useRef<RealtimeAssistantTranscriptState[]>([]);
  const seenTranscriptsRef = useRef(new Set<string>());
  const persistenceChainRef = useRef<Promise<void>>(Promise.resolve());
  const optionsRef = useRef(options);

  useEffect(() => {
    optionsRef.current = options;
  }, [options]);

  const updateVoiceState = useCallback((next: OperationsRealtimeVoiceState) => {
    voiceStateRef.current = next;
    setVoiceState(next);
  }, []);

  const reportError = useCallback((message: string) => {
    optionsRef.current.onError?.(message);
  }, []);

  const stopVoice = useCallback(() => {
    connectionGenerationRef.current += 1;
    microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
    microphoneStreamRef.current = null;
    const dataChannel = dataChannelRef.current;
    dataChannelRef.current = null;
    dataChannel?.close();
    const peerConnection = peerConnectionRef.current;
    peerConnectionRef.current = null;
    peerConnection?.close();
    if (audioElementRef.current) {
      audioElementRef.current.pause();
      audioElementRef.current.srcObject = null;
      audioElementRef.current.remove();
      audioElementRef.current = null;
    }
    updateVoiceState('idle');
  }, [updateVoiceState]);

  const flushTranscriptQueues = useCallback(() => {
    const paired = pairReadyRealtimeTranscripts(usersRef.current, assistantsRef.current);
    usersRef.current = paired.remainingUsers;
    assistantsRef.current = paired.remainingAssistants;
    const sessionId = sessionIdRef.current;

    if (paired.dropped > 0) {
      reportError('One realtime exchange could not be saved because a completed transcript was unavailable.');
    }

    for (const turn of paired.pairs) {
      persistenceChainRef.current = persistenceChainRef.current
        .then(async () => {
          await persistOperationsRealtimeTurn({
            sessionId,
            userItemId: turn.user.sourceId,
            responseId: turn.assistant.sourceId,
            userTranscript: turn.user.transcript,
            assistantTranscript: turn.assistant.transcript,
          });
          optionsRef.current.onTurnPersisted?.();
        })
        .catch((requestError) => {
          reportError(
            requestError instanceof Error
              ? requestError.message
              : 'The completed voice turn could not be saved.',
          );
        });
    }
  }, [reportError]);

  const queueRealtimeTranscriptEvent = useCallback((payload: Record<string, unknown>): boolean => {
    const userUpdate = parseRealtimeUserTranscriptState(payload);
    if (userUpdate) {
      const terminalKey = `user:${userUpdate.sourceId}`;
      if (seenTranscriptsRef.current.has(terminalKey)) return true;
      if (userUpdate.status !== 'pending') seenTranscriptsRef.current.add(terminalKey);
      usersRef.current = upsertRealtimeUserTranscript(usersRef.current, userUpdate);
      flushTranscriptQueues();
      return true;
    }

    const transcript = parseCompletedRealtimeTranscript(payload);
    if (transcript?.role === 'assistant') {
      const terminalKey = `assistant:${transcript.sourceId}`;
      if (seenTranscriptsRef.current.has(terminalKey)) return true;
      seenTranscriptsRef.current.add(terminalKey);
      assistantsRef.current = upsertRealtimeAssistantTranscript(assistantsRef.current, {
        sourceId: transcript.sourceId,
        status: 'completed',
        transcript: transcript.transcript,
      });
      flushTranscriptQueues();
      return true;
    }

    const assistantFailure = parseRealtimeAssistantFailure(payload);
    if (assistantFailure) {
      const terminalKey = `assistant:${assistantFailure.sourceId}`;
      if (seenTranscriptsRef.current.has(terminalKey)) return true;
      seenTranscriptsRef.current.add(terminalKey);
      assistantsRef.current = upsertRealtimeAssistantTranscript(assistantsRef.current, assistantFailure);
      flushTranscriptQueues();
      return true;
    }
    return false;
  }, [flushTranscriptQueues]);

  const handleRealtimeEvent = useCallback(async (
    payload: Record<string, unknown>,
    dataChannel: RTCDataChannel,
  ) => {
    if (queueRealtimeTranscriptEvent(payload)) return;

    if (payload.type === 'error') {
      const error = recordValue(payload.error);
      const message = typeof error?.message === 'string' ? error.message : 'The realtime voice session reported an error.';
      reportError(message);
      return;
    }

    if (payload.type !== 'response.function_call_arguments.done') return;
    const callId = typeof payload.call_id === 'string' ? payload.call_id : '';
    const name = typeof payload.name === 'string' ? payload.name : '';
    if (!callId || !name) return;
    let args: Record<string, unknown> = {};
    try {
      const parsed = JSON.parse(typeof payload.arguments === 'string' ? payload.arguments : '{}');
      args = recordValue(parsed) || {};
    } catch {
      args = {};
    }

    let output: Record<string, unknown>;
    try {
      output = await runOperationsRealtimeTool(name, args);
    } catch (toolError) {
      output = {
        status: 'error',
        reason: toolError instanceof Error ? toolError.message : 'The realtime operation failed.',
      };
    }
    if (dataChannel.readyState !== 'open') return;
    dataChannel.send(JSON.stringify({
      type: 'conversation.item.create',
      item: {
        type: 'function_call_output',
        call_id: callId,
        output: JSON.stringify(output),
      },
    }));
    dataChannel.send(JSON.stringify({ type: 'response.create' }));
  }, [queueRealtimeTranscriptEvent, reportError]);

  const startVoice = useCallback(async () => {
    if (voiceStateRef.current !== 'idle') return;
    if (!navigator.mediaDevices?.getUserMedia) {
      reportError('This browser does not provide microphone access for realtime voice.');
      return;
    }

    updateVoiceState('connecting');
    const generation = connectionGenerationRef.current + 1;
    connectionGenerationRef.current = generation;
    sessionIdRef.current = realtimeSessionId();
    usersRef.current = [];
    assistantsRef.current = [];
    seenTranscriptsRef.current.clear();

    try {
      const microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (connectionGenerationRef.current !== generation) {
        microphoneStream.getTracks().forEach((track) => track.stop());
        return;
      }
      microphoneStreamRef.current = microphoneStream;

      const peerConnection = new RTCPeerConnection();
      peerConnectionRef.current = peerConnection;
      const audioElement = document.createElement('audio');
      audioElement.autoplay = true;
      audioElement.setAttribute('playsinline', 'true');
      audioElement.hidden = true;
      document.body.appendChild(audioElement);
      audioElementRef.current = audioElement;
      peerConnection.ontrack = (event) => {
        audioElement.srcObject = event.streams[0];
        void audioElement.play().catch(() => undefined);
      };
      peerConnection.onconnectionstatechange = () => {
        if (peerConnectionRef.current !== peerConnection) return;
        if (peerConnection.connectionState === 'connected') updateVoiceState('live');
        if (['failed', 'disconnected', 'closed'].includes(peerConnection.connectionState)) stopVoice();
      };
      microphoneStream.getAudioTracks().forEach((track) => peerConnection.addTrack(track, microphoneStream));
      const dataChannel = peerConnection.createDataChannel('oai-events');
      dataChannelRef.current = dataChannel;
      dataChannel.onmessage = (event) => {
        let payload: Record<string, unknown> | null = null;
        try {
          payload = recordValue(JSON.parse(String(event.data)));
        } catch {
          payload = null;
        }
        if (payload) void handleRealtimeEvent(payload, dataChannel);
      };

      const offer = await peerConnection.createOffer();
      await peerConnection.setLocalDescription(offer);
      if (!offer.sdp) throw new Error('The browser could not create a realtime audio offer.');
      const answerSdp = await createOperationsRealtimeSession(offer.sdp);
      if (connectionGenerationRef.current !== generation) return;
      await peerConnection.setRemoteDescription({ type: 'answer', sdp: answerSdp });
    } catch (requestError) {
      if (connectionGenerationRef.current !== generation) return;
      stopVoice();
      if (requestError instanceof DOMException && requestError.name === 'NotAllowedError') {
        reportError('Microphone access was declined. Allow microphone access in this browser to use realtime voice.');
      } else {
        reportError(requestError instanceof Error ? requestError.message : 'Realtime voice could not start.');
      }
    }
  }, [handleRealtimeEvent, reportError, stopVoice, updateVoiceState]);

  useEffect(() => () => stopVoice(), [stopVoice]);

  return { voiceState, startVoice, stopVoice };
}
