import { FormEvent, KeyboardEvent, ReactNode, useEffect, useRef, useState } from 'react';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import {
  Ban,
  Bot,
  CircleStop,
  History,
  Mic,
  PhoneOff,
  RefreshCw,
  Send,
  ShieldCheck,
  Trash2,
  UserRound,
  Wifi,
  WifiOff,
} from 'lucide-react';
import {
  getOperationsChatMessages,
  listAgentConsoleRuns,
  type AgentConsoleRun,
  type OperationsChatMessage,
} from './api';
import {
  buildAgentWebSocketUrl,
  formatAgentTerminalMessage,
  isRunAcknowledgementFrame,
  nextAgentConsoleState,
  parseAgentConsoleFrame,
  reconnectingAgentConsoleState,
  sanitizeTerminalChunk,
  shouldResendAgentCancellation,
  type AgentConsoleFrame,
  type AgentConsoleState,
} from './agentConsoleProtocol';
import { useOperationsRealtimeVoice } from './useOperationsRealtimeVoice';

const ACTIVE_STATES = new Set<AgentConsoleState>(['connecting', 'running', 'cancelling', 'disconnected']);
const RUN_STORAGE_KEY = 'assistant-ui-agent-console-run';
const AGENT_URL_PATTERN = /(https?:\/\/[^\s<>\])]+)/g;

function renderLinkedText(content: string): ReactNode[] {
  return content.split(AGENT_URL_PATTERN).map((part, index) => (
    part.startsWith('https://') || part.startsWith('http://')
      ? <a key={`${part}-${index}`} href={part} target="_blank" rel="noreferrer" className="font-semibold text-indigo-300 underline decoration-indigo-500 underline-offset-2">{part}</a>
      : part
  ));
}

function requestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (character) => {
    const random = Math.floor(Math.random() * 16);
    const value = character === 'x' ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

function stateLabel(state: AgentConsoleState): string {
  return {
    idle: 'Ready',
    connecting: 'Connecting',
    running: 'Running',
    cancelling: 'Cancelling',
    completed: 'Completed',
    cancelled: 'Cancelled',
    failed: 'Failed',
    step_limit: 'Step limit reached',
    disconnected: 'Reconnecting',
    unauthorized: 'Sign-in required',
  }[state];
}

function stateTone(state: AgentConsoleState): string {
  if (state === 'completed') return 'border-emerald-300 bg-emerald-50 text-emerald-800';
  if (state === 'failed' || state === 'unauthorized') return 'border-rose-300 bg-rose-50 text-rose-800';
  if (state === 'cancelled' || state === 'step_limit') return 'border-amber-300 bg-amber-50 text-amber-800';
  if (ACTIVE_STATES.has(state)) return 'border-indigo-300 bg-indigo-50 text-indigo-800';
  return 'border-slate-300 bg-white text-slate-700';
}

export default function AgentConsole() {
  const terminalHostRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);
  const mountedRef = useRef(false);
  const runIdRef = useRef<string | null>(null);
  const sequenceRef = useRef(0);
  const stateRef = useRef<AgentConsoleState>('idle');
  const reconnectRef = useRef(false);
  const cancelRequestedRef = useRef(false);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  const [objective, setObjective] = useState('');
  const [messages, setMessages] = useState<OperationsChatMessage[]>([]);
  const [conversationLoading, setConversationLoading] = useState(true);
  const [runState, setRunState] = useState<AgentConsoleState>('idle');
  const [runId, setRunId] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [maxSteps, setMaxSteps] = useState(15);
  const [statusMessage, setStatusMessage] = useState('Ask naturally. The agent remembers this conversation and works through the authorised steps.');
  const [error, setError] = useState<string | null>(null);
  const [recentRuns, setRecentRuns] = useState<AgentConsoleRun[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [consoleEnabled, setConsoleEnabled] = useState(true);

  const setState = (next: AgentConsoleState) => {
    stateRef.current = next;
    setRunState(next);
  };

  const writeConsole = (message: string, colour = '\x1b[0;37m') => {
    const terminal = terminalRef.current;
    if (!terminal) return;
    const safe = sanitizeTerminalChunk(message).replace(/\n/g, '\r\n');
    terminal.write(`${colour}${safe}\x1b[0m\r\n`);
  };

  const refreshHistory = async () => {
    setHistoryLoading(true);
    try {
      const result = await listAgentConsoleRuns();
      if (!mountedRef.current) return;
      setRecentRuns(result.runs);
      setConsoleEnabled(result.enabled);
    } catch (requestError) {
      if (mountedRef.current) {
        setError(requestError instanceof Error ? requestError.message : 'Could not load run history.');
      }
    } finally {
      if (mountedRef.current) setHistoryLoading(false);
    }
  };

  const refreshConversation = async (showLoading = false) => {
    if (showLoading) setConversationLoading(true);
    try {
      const result = await getOperationsChatMessages();
      if (mountedRef.current) setMessages(result.messages);
    } catch (requestError) {
      if (mountedRef.current) {
        setError(requestError instanceof Error ? requestError.message : 'Could not load the agent conversation.');
      }
    } finally {
      if (mountedRef.current) setConversationLoading(false);
    }
  };

  const { voiceState, startVoice, stopVoice } = useOperationsRealtimeVoice({
    onTurnPersisted: () => { void refreshConversation(); },
    onError: setError,
  });

  const handleFrame = (frame: AgentConsoleFrame) => {
    if (typeof frame.sequence === 'number') {
      if (frame.sequence <= sequenceRef.current) return;
      sequenceRef.current = frame.sequence;
    }
    if (frame.type === 'ready') {
      if (typeof frame.enabled === 'boolean') setConsoleEnabled(frame.enabled);
      if (typeof frame.limits?.maxSteps === 'number') setMaxSteps(frame.limits.maxSteps);
      return;
    }
    if (isRunAcknowledgementFrame(frame)) reconnectAttemptRef.current = 0;
    if (frame.runId) {
      runIdRef.current = frame.runId;
      setRunId(frame.runId);
      sessionStorage.setItem(RUN_STORAGE_KEY, frame.runId);
    }
    if (typeof frame.step === 'number') setStep(frame.step);
    if (typeof frame.maxSteps === 'number') setMaxSteps(frame.maxSteps);
    if (frame.status === 'cancelling') cancelRequestedRef.current = true;
    const nextState = nextAgentConsoleState(stateRef.current, frame);
    setState(nextState);

    if (frame.message) {
      if (frame.type !== 'terminal') setStatusMessage(frame.message);
      if (frame.type === 'terminal') {
        writeConsole(
          formatAgentTerminalMessage(frame.message),
          frame.stream === 'stderr' ? '\x1b[1;31m' : '\x1b[0;37m',
        );
      } else if (frame.type === 'completed') {
        writeConsole(`✓ ${frame.message}`, '\x1b[1;32m');
      } else if (frame.type === 'error') {
        setError(frame.message);
        writeConsole(`ERROR: ${frame.message}`, '\x1b[1;31m');
      } else if (frame.type === 'cancelled' || frame.type === 'limit_reached') {
        writeConsole(frame.message, '\x1b[1;33m');
      } else {
        writeConsole(frame.message, '\x1b[1;33m');
      }
    }
    if (['completed', 'cancelled', 'limit_reached', 'error'].includes(frame.type)) {
      cancelRequestedRef.current = false;
      reconnectRef.current = false;
      sessionStorage.removeItem(RUN_STORAGE_KEY);
      void refreshHistory();
      void refreshConversation();
    }
  };

  const connectSocket = (
    mode: 'start' | 'attach',
    options: { objective?: string; requestId?: string; runId?: string; afterSequence?: number },
  ) => {
    if (!mountedRef.current) return;
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    socketRef.current?.close();
    if (mode === 'attach' && cancelRequestedRef.current) setState('cancelling');
    else setState('connecting');
    setError(null);
    let commandSent = false;
    const socket = new WebSocket(buildAgentWebSocketUrl(window.location));
    socketRef.current = socket;

    socket.onmessage = (event) => {
      if (socketRef.current !== socket) return;
      const frame = parseAgentConsoleFrame(event.data);
      if (!frame) return;
      handleFrame(frame);
      if (frame.type === 'ready' && !commandSent && socket.readyState === WebSocket.OPEN) {
        commandSent = true;
        socket.send(JSON.stringify(
          mode === 'start'
            ? {
                type: 'start',
                requestId: options.requestId,
                objective: options.objective,
                afterSequence: options.afterSequence || 0,
              }
            : { type: 'attach', runId: options.runId, afterSequence: options.afterSequence || 0 },
        ));
        if (shouldResendAgentCancellation(mode, cancelRequestedRef.current, options.runId)) {
          socket.send(JSON.stringify({ type: 'cancel', runId: options.runId }));
        }
      }
    };
    socket.onerror = () => {
      if (socketRef.current !== socket) return;
      if (mountedRef.current) setError('The live Operations Console connection failed.');
    };
    socket.onclose = (event) => {
      if (socketRef.current !== socket) return;
      socketRef.current = null;
      if (!mountedRef.current) return;
      if (event.code === 4401) {
        reconnectRef.current = false;
        setState('unauthorized');
        setError('Your admin session has expired. Sign in again to use the Operations Console.');
        window.dispatchEvent(new Event('admin-auth-required'));
        return;
      }
      if (event.code === 4403) {
        reconnectRef.current = false;
        setState('failed');
        setError('The server rejected this page origin. Open the console from the application’s normal address.');
        return;
      }
      const activeRunId = runIdRef.current;
      if (reconnectRef.current && activeRunId && ACTIVE_STATES.has(stateRef.current)) {
        setState(reconnectingAgentConsoleState(cancelRequestedRef.current));
        if (cancelRequestedRef.current) {
          setStatusMessage('Cancellation is still requested. Reattaching to confirm it…');
        } else {
          setStatusMessage('Connection interrupted. Reattaching to the same run…');
        }
        reconnectAttemptRef.current += 1;
        const delay = Math.min(15_000, 1_500 * (2 ** Math.min(3, reconnectAttemptRef.current - 1)));
        reconnectTimerRef.current = window.setTimeout(() => {
          connectSocket('attach', { runId: activeRunId, afterSequence: sequenceRef.current });
        }, delay);
      } else if (
        reconnectRef.current
        && mode === 'start'
        && options.requestId
        && options.objective
        && ACTIVE_STATES.has(stateRef.current)
      ) {
        if (reconnectAttemptRef.current >= 4) {
          reconnectRef.current = false;
          setState('failed');
          setStatusMessage('The server did not acknowledge the run. Check your sign-in and connection, then try again.');
          setError('Could not establish an authenticated Operations Console session.');
          void refreshHistory();
          void refreshConversation();
          return;
        }
        setState('disconnected');
        setStatusMessage('Connection interrupted before acknowledgement. Retrying the same idempotent request…');
        reconnectAttemptRef.current += 1;
        const delay = Math.min(15_000, 1_500 * (2 ** Math.min(3, reconnectAttemptRef.current - 1)));
        reconnectTimerRef.current = window.setTimeout(() => {
          connectSocket('start', {
            objective: options.objective,
            requestId: options.requestId,
            afterSequence: sequenceRef.current,
          });
        }, delay);
      }
    };
  };

  const startRun = (event?: FormEvent) => {
    event?.preventDefault();
    const cleanObjective = objective.trim();
    if (
      !cleanObjective
      || ACTIVE_STATES.has(stateRef.current)
      || !consoleEnabled
      || conversationLoading
      || historyLoading
      || voiceState !== 'idle'
    ) return;
    const currentRequestId = requestId();
    const optimisticMessage: OperationsChatMessage = {
      id: `pending-${currentRequestId}`,
      role: 'user',
      content: cleanObjective,
      createdAt: new Date().toISOString(),
    };
    setMessages((current) => [...current.filter((item) => !item.id.startsWith('pending-')), optimisticMessage]);
    setObjective('');
    terminalRef.current?.clear();
    sequenceRef.current = 0;
    runIdRef.current = null;
    setRunId(null);
    setStep(0);
    setStatusMessage('Connecting to the Operations Coding Agent…');
    cancelRequestedRef.current = false;
    reconnectRef.current = true;
    reconnectAttemptRef.current = 0;
    connectSocket('start', { objective: cleanObjective, requestId: currentRequestId });
  };

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      startRun();
    }
  };

  const cancelRun = () => {
    const socket = socketRef.current;
    const activeRunId = runIdRef.current;
    if (!activeRunId || stateRef.current === 'cancelling') return;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setError('The console is reconnecting. Try Cancel again once it is connected.');
      return;
    }
    cancelRequestedRef.current = true;
    setState('cancelling');
    setStatusMessage('Cancellation requested…');
    socket.send(JSON.stringify({ type: 'cancel', runId: activeRunId }));
  };

  const attachRun = (selected: AgentConsoleRun) => {
    if (ACTIVE_STATES.has(stateRef.current)) return;
    terminalRef.current?.clear();
    sequenceRef.current = 0;
    runIdRef.current = selected.id;
    setRunId(selected.id);
    setStep(0);
    cancelRequestedRef.current = selected.cancelRequested;
    reconnectRef.current = selected.status === 'starting' || selected.status === 'running';
    sessionStorage.setItem(RUN_STORAGE_KEY, selected.id);
    connectSocket('attach', { runId: selected.id, afterSequence: 0 });
  };

  useEffect(() => {
    const host = terminalHostRef.current;
    if (!host) return undefined;
    const terminal = new Terminal({
      disableStdin: true,
      convertEol: true,
      cursorBlink: false,
      scrollback: 5_000,
      fontSize: 12,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
      theme: {
        background: '#020617',
        foreground: '#e2e8f0',
        cursor: '#818cf8',
        selectionBackground: '#334155',
      },
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(host);
    terminalRef.current = terminal;
    const fit = () => window.requestAnimationFrame(() => {
      try { fitAddon.fit(); } catch { /* A hidden route can briefly have zero dimensions. */ }
    });
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(host);
    terminal.writeln('\x1b[1;36mAssistant UI — Conversational Coding Agent\x1b[0m');
    terminal.writeln('\x1b[0;90mAuthenticated · persistent context · audited actions · bounded execution\x1b[0m');
    terminal.writeln('');
    return () => {
      observer.disconnect();
      fitAddon.dispose();
      terminal.dispose();
      if (terminalRef.current === terminal) terminalRef.current = null;
    };
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void refreshHistory();
    void refreshConversation(true);
    const savedRunId = sessionStorage.getItem(RUN_STORAGE_KEY);
    if (savedRunId) {
      runIdRef.current = savedRunId;
      setRunId(savedRunId);
      reconnectRef.current = true;
      connectSocket('attach', { runId: savedRunId, afterSequence: 0 });
    }
    return () => {
      mountedRef.current = false;
      reconnectRef.current = false;
      if (reconnectTimerRef.current !== null) window.clearTimeout(reconnectTimerRef.current);
      socketRef.current?.close();
      socketRef.current = null;
    };
    // The socket lifecycle is intentionally owned by this mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [messages, runState]);

  const active = ACTIVE_STATES.has(runState);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-gradient-to-r from-slate-950 via-indigo-950 to-slate-950 px-4 py-4 sm:px-6">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <div className="rounded-xl bg-indigo-500/15 p-2.5 ring-1 ring-indigo-300/20">
              <Bot className="h-5 w-5 text-indigo-200" />
            </div>
            <div>
              <h1 className="text-base font-black">Operations Coding Agent</h1>
              <p className="mt-0.5 text-[11px] text-slate-400">A persistent conversation with live evidence, durable memory and reviewed cloud coding.</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {voiceState === 'idle' ? (
              <button
                type="button"
                onClick={() => { setError(null); void startVoice(); }}
                disabled={active || conversationLoading || historyLoading}
                data-testid="agent-voice-start"
                className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-indigo-300/30 bg-indigo-300/10 px-3 text-[10px] font-bold text-indigo-100 transition hover:bg-indigo-300/20 disabled:opacity-40"
              >
                <Mic className="h-3.5 w-3.5" /> Start realtime voice
              </button>
            ) : (
              <button
                type="button"
                onClick={stopVoice}
                data-testid="agent-voice-stop"
                className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-rose-300/30 bg-rose-300/10 px-3 text-[10px] font-bold text-rose-100 transition hover:bg-rose-300/20"
              >
                {voiceState === 'connecting' ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <PhoneOff className="h-3.5 w-3.5" />}
                {voiceState === 'connecting' ? 'Connecting voice…' : 'End voice'}
              </button>
            )}
            <button
              type="button"
              onClick={() => { void refreshConversation(true); void refreshHistory(); }}
              disabled={conversationLoading || historyLoading || active}
              className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 text-[10px] font-bold text-slate-300 hover:bg-white/10 disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${conversationLoading || historyLoading ? 'animate-spin' : ''}`} /> Refresh
            </button>
            <div className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[10px] font-black uppercase tracking-wide ${stateTone(runState)}`} role="status" aria-live="polite">
              {runState === 'disconnected' ? <WifiOff className="h-3.5 w-3.5" /> : <Wifi className="h-3.5 w-3.5" />}
              {stateLabel(runState)}
            </div>
          </div>
        </div>
      </header>

      {voiceState !== 'idle' && (
        <div className="border-b border-emerald-500/30 bg-emerald-500/10 px-4 py-2.5 text-center text-[11px] font-semibold text-emerald-100" role="status" aria-live="polite">
          {voiceState === 'live'
            ? 'Realtime voice is live. Speak naturally and interrupt when needed. Voice turns are saved into this same conversation.'
            : 'Connecting the microphone and loading this persistent conversation…'}
        </div>
      )}

      <div className="mx-auto grid w-full max-w-7xl flex-1 gap-4 p-3 sm:p-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl">
          <div
            className="h-[390px] min-h-[300px] overflow-y-auto bg-slate-950/55 px-3 py-5 sm:px-5"
            role="log"
            aria-live="polite"
            aria-label="Operations coding agent conversation"
            data-testid="agent-conversation-transcript"
          >
            {conversationLoading && messages.length === 0 ? (
              <div className="flex h-full items-center justify-center gap-2 text-xs font-semibold text-indigo-300">
                <RefreshCw className="h-4 w-4 animate-spin" /> Loading conversation…
              </div>
            ) : messages.length === 0 ? (
              <div className="mx-auto flex h-full max-w-lg flex-col items-center justify-center text-center">
                <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-300/20">
                  <Bot className="h-6 w-6" />
                </div>
                <h2 className="text-sm font-black text-white">Tell me the outcome you want</h2>
                <p className="mt-2 text-xs leading-relaxed text-slate-400">
                  I can investigate the live system, inspect source, research current technical issues, remember durable lessons and run reviewed coding work.
                </p>
              </div>
            ) : (
              <div className="mx-auto flex max-w-3xl flex-col gap-4">
                {messages.map((message) => (
                  <div key={message.id} className={`flex gap-2.5 ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                    {message.role === 'assistant' && (
                      <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-300/20">
                        <Bot className="h-3.5 w-3.5" />
                      </div>
                    )}
                    <div className={`max-w-[84%] whitespace-pre-wrap rounded-2xl px-3.5 py-2.5 text-xs leading-relaxed shadow-sm ${
                      message.role === 'user'
                        ? 'rounded-br-md bg-indigo-600 text-white'
                        : 'rounded-bl-md border border-slate-700 bg-slate-900 text-slate-100'
                    } ${message.id.startsWith('pending-') ? 'opacity-70' : ''}`}>
                      {message.id.startsWith('operations-realtime:') && (
                        <span className="mb-1 flex items-center gap-1 text-[8px] font-black uppercase tracking-wider opacity-70">
                          <Mic className="h-2.5 w-2.5" /> Voice
                        </span>
                      )}
                      {message.role === 'assistant' ? renderLinkedText(message.content) : message.content}
                    </div>
                    {message.role === 'user' && (
                      <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-slate-800 text-slate-300">
                        <UserRound className="h-3.5 w-3.5" />
                      </div>
                    )}
                  </div>
                ))}
                {active && (
                  <div className="flex items-center gap-2.5">
                    <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-300/20">
                      <Bot className="h-3.5 w-3.5" />
                    </div>
                    <div className="rounded-2xl rounded-bl-md border border-indigo-500/30 bg-slate-900 px-3.5 py-2.5 text-xs font-semibold text-indigo-200 shadow-sm">
                      Working on it… live progress is shown below.
                    </div>
                  </div>
                )}
                <div ref={transcriptEndRef} />
              </div>
            )}
          </div>

          <form onSubmit={startRun} className="border-y border-slate-800 bg-slate-900 p-3 sm:p-4">
            <label htmlFor="agent-message" className="sr-only">Message the operations coding agent</label>
            <textarea
              id="agent-message"
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              disabled={active || voiceState !== 'idle'}
              rows={3}
              maxLength={8_000}
              placeholder="Message naturally — for example: Still not working. Check the earlier repair and fix it properly."
              className="w-full resize-y rounded-xl border border-slate-700 bg-slate-950 px-3 py-3 text-sm leading-relaxed text-white outline-none transition placeholder:text-slate-600 focus:border-indigo-500 disabled:opacity-60"
            />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="submit"
                disabled={active || voiceState !== 'idle' || !objective.trim() || !consoleEnabled || conversationLoading || historyLoading}
                className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 py-2.5 text-xs font-black text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:bg-slate-700"
              >
                {runState === 'connecting' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                Send
              </button>
              <button
                type="button"
                onClick={cancelRun}
                disabled={!active || !runId || runState === 'cancelling'}
                className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-rose-500/50 bg-rose-500/10 px-4 py-2.5 text-xs font-black text-rose-200 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <CircleStop className="h-4 w-4" /> Cancel
              </button>
              <span className="text-[10px] text-slate-500">Enter to send · Shift+Enter for a new line</span>
              <span className="ml-auto text-[10px] font-bold text-slate-400">Step {step}/{maxSteps}</span>
            </div>
            {!consoleEnabled && (
              <div className="mt-3 flex items-start gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-100" role="alert">
                <Ban className="mt-0.5 h-4 w-4 shrink-0" /> The coding agent is disabled or missing its server configuration.
              </div>
            )}
            {error && <div className="mt-3 rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-100" role="alert">{error}</div>}
          </form>

          <div className="flex items-center justify-between gap-3 border-b border-slate-800 bg-slate-950/70 px-4 py-2.5 text-[11px] text-slate-300">
            <span aria-live="polite">{statusMessage}</span>
            <button type="button" onClick={() => terminalRef.current?.clear()} className="inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2 py-1 text-[10px] font-bold text-slate-400 hover:bg-slate-800 hover:text-white">
              <Trash2 className="h-3.5 w-3.5" /> Clear work log
            </button>
          </div>
          <div ref={terminalHostRef} className="h-[300px] min-h-[240px] w-full bg-slate-950 p-2" aria-label="Live coding agent work log" />
          <div className="flex items-start gap-2 border-t border-slate-800 bg-slate-900 px-4 py-3 text-[10px] leading-relaxed text-slate-400">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
            <p>The work log is monitor-only. Code is changed only on an isolated review branch; protected settings and production deployment still require the exact confirmation returned by the audited workflow.</p>
          </div>
        </section>

        <aside className="min-h-0 overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
          <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
            <div className="flex items-center gap-2 text-xs font-black"><History className="h-4 w-4 text-indigo-300" /> Recent runs</div>
            <button type="button" onClick={() => void refreshHistory()} disabled={historyLoading} aria-label="Refresh work history" className="rounded-lg p-2 text-slate-400 hover:bg-slate-800 hover:text-white disabled:opacity-50">
              <RefreshCw className={`h-4 w-4 ${historyLoading ? 'animate-spin' : ''}`} />
            </button>
          </div>
          <div className="max-h-[620px] space-y-2 overflow-y-auto p-3">
            {!historyLoading && recentRuns.length === 0 && <p className="p-4 text-center text-xs text-slate-500">No coding-agent work yet.</p>}
            {recentRuns.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => attachRun(item)}
                disabled={active}
                className="w-full rounded-xl border border-slate-800 bg-slate-950/60 p-3 text-left transition hover:border-indigo-500/50 hover:bg-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] font-black uppercase tracking-wide text-indigo-300">{item.status.replace('_', ' ')}</span>
                  <span className="text-[9px] text-slate-600">{item.stepCount}/{item.maxSteps}</span>
                </div>
                <p className="mt-2 line-clamp-3 text-[11px] leading-relaxed text-slate-300">{item.objective}</p>
                <p className="mt-2 text-[9px] text-slate-600">{new Date(item.createdAt).toLocaleString()}</p>
              </button>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}
