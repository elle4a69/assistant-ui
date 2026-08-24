import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const protocolSource = await readFile(new URL('../src/operationsRealtimeVoiceProtocol.ts', import.meta.url), 'utf8');
const transpiled = ts.transpileModule(protocolSource, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const protocol = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);
const hookSource = await readFile(new URL('../src/useOperationsRealtimeVoice.ts', import.meta.url), 'utf8');
const agentSource = await readFile(new URL('../src/AgentConsole.tsx', import.meta.url), 'utf8');
const settingsSource = await readFile(new URL('../src/OperationsAIChat.tsx', import.meta.url), 'utf8');
const apiSource = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8');

test('completed user and assistant audio transcripts are recognised from official realtime events', () => {
  assert.deepEqual(protocol.parseCompletedRealtimeTranscript({
    type: 'conversation.item.input_audio_transcription.completed',
    event_id: 'event-user',
    item_id: 'item-user',
    transcript: 'Please inspect the failed deployment.',
  }), {
    role: 'user',
    sourceId: 'item-user',
    transcript: 'Please inspect the failed deployment.',
  });

  assert.deepEqual(protocol.parseCompletedRealtimeTranscript({
    type: 'response.output_audio_transcript.done',
    event_id: 'event-assistant',
    response_id: 'response-assistant',
    item_id: 'item-assistant',
    transcript: 'The last deployment passed.',
  }), {
    role: 'assistant',
    sourceId: 'response-assistant',
    transcript: 'The last deployment passed.',
  });
  assert.equal(protocol.parseCompletedRealtimeTranscript({ type: 'response.output_audio_transcript.delta' }), null);
});

test('late asynchronous user transcription is paired in owner-then-assistant order', () => {
  const assistant = protocol.parseCompletedRealtimeTranscript({
    type: 'response.output_audio_transcript.done',
    response_id: 'response-1',
    transcript: 'I found the issue.',
  });
  const user = protocol.parseCompletedRealtimeTranscript({
    type: 'conversation.item.input_audio_transcription.completed',
    item_id: 'item-1',
    transcript: 'Find the issue.',
  });

  const result = protocol.pairCompletedRealtimeTranscripts([user], [assistant]);

  assert.deepEqual(result.pairs, [{ user, assistant }]);
  assert.deepEqual(result.remainingUsers, []);
  assert.deepEqual(result.remainingAssistants, []);
});

test('a failed input transcription is dropped without shifting the next voice turn', () => {
  let users = [];
  users = protocol.upsertRealtimeUserTranscript(users, {
    sourceId: 'item-1', status: 'pending', transcript: '',
  });
  users = protocol.upsertRealtimeUserTranscript(users, {
    sourceId: 'item-1', status: 'failed', transcript: '',
  });
  users = protocol.upsertRealtimeUserTranscript(users, {
    sourceId: 'item-2', status: 'completed', transcript: 'Check the next deployment.',
  });
  const assistants = [
    { sourceId: 'response-1', status: 'completed', transcript: 'This transcript has no reliable owner input.' },
    { sourceId: 'response-2', status: 'completed', transcript: 'The next deployment passed.' },
  ];

  const result = protocol.pairReadyRealtimeTranscripts(users, assistants);

  assert.equal(result.dropped, 1);
  assert.equal(result.pairs.length, 1);
  assert.equal(result.pairs[0].user.sourceId, 'item-2');
  assert.equal(result.pairs[0].assistant.sourceId, 'response-2');
});

test('Coding Agent and Operations settings share persistent full-duplex voice controls', () => {
  assert.match(agentSource, /useOperationsRealtimeVoice/);
  assert.match(agentSource, /data-testid="agent-voice-start"/);
  assert.match(agentSource, /voiceState === 'live'/);
  assert.match(agentSource, /operations-realtime:/);
  assert.match(settingsSource, /useOperationsRealtimeVoice/);
  assert.match(hookSource, /persistOperationsRealtimeTurn/);
  assert.match(hookSource, /parseCompletedRealtimeTranscript/);
  assert.match(hookSource, /response\.function_call_arguments\.done/);
  assert.match(hookSource, /getUserMedia\(\{ audio: true \}\)/);
  assert.match(apiSource, /operations-chat\/realtime\/turns/);
  assert.doesNotMatch(settingsSource, /Voice is not added to persistent text history/);
});
