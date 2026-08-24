import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const protocolSource = await readFile(new URL('../src/agentConsoleProtocol.ts', import.meta.url), 'utf8');
const transpiled = ts.transpileModule(protocolSource, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const protocol = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);
const viewSource = await readFile(new URL('../src/AgentConsole.tsx', import.meta.url), 'utf8');
const appSource = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8');
const viteSource = await readFile(new URL('../vite.config.ts', import.meta.url), 'utf8');

test('production websocket URL is same-origin and never exposes Fly port 8080', () => {
  assert.equal(
    protocol.buildAgentWebSocketUrl({ protocol: 'https:', host: 'assistant-ui-hub.fly.dev' }),
    'wss://assistant-ui-hub.fly.dev/ws/agent',
  );
  assert.equal(
    protocol.buildAgentWebSocketUrl({ protocol: 'http:', host: 'localhost:5190' }),
    'ws://localhost:5190/ws/agent',
  );
  assert.doesNotMatch(protocolSource + viewSource, /hostname}:8080|fly\.dev:8080/);
  assert.match(viteSource, /['"]\/ws['"]/);
  assert.match(viteSource, /ws:\s*true/);
});

test('protocol rejects malformed or unsequenced persisted frames', () => {
  assert.equal(protocol.parseAgentConsoleFrame('not json'), null);
  assert.equal(protocol.parseAgentConsoleFrame(JSON.stringify({ type: 'unknown' })), null);
  assert.equal(protocol.parseAgentConsoleFrame(JSON.stringify({
    type: 'status', runId: 'run-one', message: 'Working',
  })), null);

  assert.deepEqual(protocol.parseAgentConsoleFrame(JSON.stringify({
    type: 'status', runId: 'run-one', sequence: 2, step: 1, message: 'Working',
  })), {
    type: 'status', runId: 'run-one', sequence: 2, step: 1, message: 'Working',
  });
});

test('terminal state transitions are explicit and cancellation is not overwritten by progress', () => {
  assert.equal(protocol.nextAgentConsoleState('connecting', {
    type: 'run_started', runId: 'run-one', sequence: 1,
  }), 'running');
  assert.equal(protocol.nextAgentConsoleState('cancelling', {
    type: 'status', runId: 'run-one', sequence: 2,
  }), 'cancelling');
  assert.equal(protocol.nextAgentConsoleState('disconnected', {
    type: 'status', runId: 'run-one', sequence: 2, status: 'cancelling',
  }), 'cancelling');
  assert.equal(protocol.nextAgentConsoleState('running', {
    type: 'completed', runId: 'run-one', sequence: 3,
  }), 'completed');
  assert.equal(protocol.nextAgentConsoleState('running', {
    type: 'limit_reached', runId: 'run-one', sequence: 3,
  }), 'step_limit');
});

test('transport ready does not reset reconnect backoff before the run is acknowledged', () => {
  assert.equal(protocol.isRunAcknowledgementFrame({ type: 'ready' }), false);
  assert.equal(protocol.isRunAcknowledgementFrame({
    type: 'run_started', runId: 'run-one', sequence: 1,
  }), true);
  assert.match(viewSource, /isRunAcknowledgementFrame\(frame\).*reconnectAttemptRef\.current = 0/);
});

test('cancellation remains pending across disconnect and is resent after attach', () => {
  assert.equal(protocol.reconnectingAgentConsoleState(true), 'cancelling');
  assert.equal(protocol.reconnectingAgentConsoleState(false), 'disconnected');
  assert.equal(protocol.shouldResendAgentCancellation('attach', true, 'run-one'), true);
  assert.equal(protocol.shouldResendAgentCancellation('attach', false, 'run-one'), false);
  assert.equal(protocol.shouldResendAgentCancellation('start', true, 'run-one'), false);
});

test('terminal chunks strip dangerous controls before xterm rendering', () => {
  const cleaned = protocol.sanitizeTerminalChunk('safe\u0000\u001b]52;c;copy\u0007\u009b31mend');
  assert.equal(cleaned.includes('\u0000'), false);
  assert.equal(cleaned.includes('\u001b'), false);
  assert.equal(cleaned.includes('\u009b'), false);
});

test('structured terminal results are rendered as readable text instead of raw JSON', () => {
  const formatted = protocol.formatAgentTerminalMessage(
    '$ ops inspect_system_status\n'
      + '{"status":"ok","service":"assistant-ui","checks":[{"name":"database","healthy":true}],"warnings":[]}',
  );

  assert.equal(formatted, [
    'Result: Inspect system status',
    'Status: ok',
    'Service: assistant-ui',
    'Checks:',
    '  1.',
    '    Name: database',
    '    Healthy: Yes',
    'Warnings:',
    '  None',
  ].join('\n'));
  assert.doesNotMatch(formatted, /[{}"]|"status"/);
  assert.equal(
    protocol.formatAgentTerminalMessage('$ read main:notes.txt\nplain text output'),
    '$ read main:notes.txt\nplain text output',
  );
});

test('Agent Runner is monitor-only, responsive and cleans up browser resources', () => {
  assert.match(viewSource, /from '@xterm\/xterm'/);
  assert.match(viewSource, /from '@xterm\/addon-fit'/);
  assert.match(viewSource, /disableStdin:\s*true/);
  assert.match(viewSource, /new ResizeObserver/);
  assert.match(viewSource, /observer\.disconnect\(\)/);
  assert.match(viewSource, /terminal\.dispose\(\)/);
  assert.match(viewSource, /socketRef\.current\?\.close\(\)/);
  assert.match(viewSource, /socketRef\.current !== socket/);
  assert.match(viewSource, /connectSocket\('start'/);
  assert.match(viewSource, /reconnectAttemptRef\.current >= 4/);
  assert.match(viewSource, /cancelRequestedRef\.current = true/);
  assert.match(viewSource, /shouldResendAgentCancellation\(mode, cancelRequestedRef\.current/);
  assert.match(viewSource, /formatAgentTerminalMessage\(frame\.message\)/);
  assert.match(viewSource, /socket\.send\(JSON\.stringify\(\{ type: 'cancel', runId: options\.runId \}\)\)/);
  assert.match(viewSource, /type:\s*'cancel'/);
  assert.match(viewSource, /sessionStorage/);
  assert.match(appSource, /\/agent-console/);
  assert.match(appSource, /Agent Runner/);
});
