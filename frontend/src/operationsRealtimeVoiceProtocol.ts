export interface CompletedRealtimeTranscript {
  role: 'user' | 'assistant';
  sourceId: string;
  transcript: string;
}

export interface CompletedRealtimeTurn {
  user: CompletedRealtimeTranscript;
  assistant: CompletedRealtimeTranscript;
}

export interface RealtimeUserTranscriptState {
  sourceId: string;
  status: 'pending' | 'completed' | 'failed';
  transcript: string;
}

export interface RealtimeAssistantTranscriptState {
  sourceId: string;
  status: 'completed' | 'failed';
  transcript: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function cleanString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

export function parseCompletedRealtimeTranscript(value: unknown): CompletedRealtimeTranscript | null {
  const event = asRecord(value);
  if (!event) return null;
  const type = cleanString(event.type);

  if (type === 'conversation.item.input_audio_transcription.completed') {
    const transcript = cleanString(event.transcript);
    const sourceId = cleanString(event.item_id) || cleanString(event.event_id);
    return transcript && sourceId ? { role: 'user', sourceId, transcript } : null;
  }

  if (
    type === 'response.output_audio_transcript.done'
    || type === 'response.audio_transcript.done'
    || type === 'response.output_text.done'
  ) {
    const transcript = cleanString(event.transcript) || cleanString(event.text);
    const sourceId = cleanString(event.response_id) || cleanString(event.item_id) || cleanString(event.event_id);
    return transcript && sourceId ? { role: 'assistant', sourceId, transcript } : null;
  }

  return null;
}

export function parseRealtimeUserTranscriptState(value: unknown): RealtimeUserTranscriptState | null {
  const event = asRecord(value);
  if (!event) return null;
  const type = cleanString(event.type);
  const sourceId = cleanString(event.item_id) || cleanString(event.event_id);
  if (!sourceId) return null;
  if (type === 'input_audio_buffer.committed') {
    return { sourceId, status: 'pending', transcript: '' };
  }
  if (type === 'conversation.item.input_audio_transcription.failed') {
    return { sourceId, status: 'failed', transcript: '' };
  }
  if (type === 'conversation.item.input_audio_transcription.completed') {
    const transcript = cleanString(event.transcript);
    return transcript ? { sourceId, status: 'completed', transcript } : { sourceId, status: 'failed', transcript: '' };
  }
  return null;
}

export function parseRealtimeAssistantFailure(value: unknown): RealtimeAssistantTranscriptState | null {
  const event = asRecord(value);
  if (!event || cleanString(event.type) !== 'response.done') return null;
  const response = asRecord(event.response);
  if (!response || !['failed', 'cancelled', 'incomplete'].includes(cleanString(response.status))) return null;
  const sourceId = cleanString(response.id) || cleanString(event.event_id);
  return sourceId ? { sourceId, status: 'failed', transcript: '' } : null;
}

export function upsertRealtimeUserTranscript(
  current: RealtimeUserTranscriptState[],
  update: RealtimeUserTranscriptState,
): RealtimeUserTranscriptState[] {
  const index = current.findIndex((item) => item.sourceId === update.sourceId);
  if (index < 0) return [...current, update];
  if (current[index].status !== 'pending' && update.status === 'pending') return current;
  return current.map((item, itemIndex) => itemIndex === index ? update : item);
}

export function upsertRealtimeAssistantTranscript(
  current: RealtimeAssistantTranscriptState[],
  update: RealtimeAssistantTranscriptState,
): RealtimeAssistantTranscriptState[] {
  const index = current.findIndex((item) => item.sourceId === update.sourceId);
  if (index < 0) return [...current, update];
  if (current[index].status === 'completed' && update.status === 'failed') return current;
  return current.map((item, itemIndex) => itemIndex === index ? update : item);
}

export function pairReadyRealtimeTranscripts(
  users: RealtimeUserTranscriptState[],
  assistants: RealtimeAssistantTranscriptState[],
): {
  pairs: CompletedRealtimeTurn[];
  remainingUsers: RealtimeUserTranscriptState[];
  remainingAssistants: RealtimeAssistantTranscriptState[];
  dropped: number;
} {
  const remainingUsers = [...users];
  const remainingAssistants = [...assistants];
  const pairs: CompletedRealtimeTurn[] = [];
  let dropped = 0;

  while (remainingUsers.length > 0 && remainingAssistants.length > 0) {
    if (remainingUsers[0].status === 'pending') break;
    const user = remainingUsers.shift()!;
    const assistant = remainingAssistants.shift()!;
    if (user.status !== 'completed' || assistant.status !== 'completed') {
      dropped += 1;
      continue;
    }
    pairs.push({
      user: { role: 'user', sourceId: user.sourceId, transcript: user.transcript },
      assistant: { role: 'assistant', sourceId: assistant.sourceId, transcript: assistant.transcript },
    });
  }
  return { pairs, remainingUsers, remainingAssistants, dropped };
}

export function pairCompletedRealtimeTranscripts(
  users: CompletedRealtimeTranscript[],
  assistants: CompletedRealtimeTranscript[],
): {
  pairs: CompletedRealtimeTurn[];
  remainingUsers: CompletedRealtimeTranscript[];
  remainingAssistants: CompletedRealtimeTranscript[];
} {
  const paired = pairReadyRealtimeTranscripts(
    users.map((item) => ({ sourceId: item.sourceId, status: 'completed', transcript: item.transcript })),
    assistants.map((item) => ({ sourceId: item.sourceId, status: 'completed', transcript: item.transcript })),
  );
  return {
    pairs: paired.pairs,
    remainingUsers: paired.remainingUsers.map((item) => ({
      role: 'user' as const,
      sourceId: item.sourceId,
      transcript: item.transcript,
    })),
    remainingAssistants: paired.remainingAssistants.map((item) => ({
      role: 'assistant' as const,
      sourceId: item.sourceId,
      transcript: item.transcript,
    })),
  };
}
