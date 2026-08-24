export type AgentConsoleState =
  | 'idle'
  | 'connecting'
  | 'running'
  | 'cancelling'
  | 'completed'
  | 'cancelled'
  | 'failed'
  | 'step_limit'
  | 'disconnected'
  | 'unauthorized';

export interface AgentConsoleFrame {
  type: 'ready' | 'run_started' | 'status' | 'terminal' | 'completed' | 'cancelled' | 'limit_reached' | 'error';
  runId?: string;
  sequence?: number;
  step?: number | null;
  maxSteps?: number;
  message?: string;
  stream?: 'stdout' | 'stderr';
  status?: string;
  summary?: string | null;
  code?: string;
  retryable?: boolean;
  enabled?: boolean;
  protocolVersion?: number;
  limits?: {
    maxSteps?: number;
    actionTimeoutSeconds?: number;
    totalTimeoutSeconds?: number;
  };
}

const FRAME_TYPES = new Set<AgentConsoleFrame['type']>([
  'ready',
  'run_started',
  'status',
  'terminal',
  'completed',
  'cancelled',
  'limit_reached',
  'error',
]);

export function buildAgentWebSocketUrl(locationLike: Pick<Location, 'protocol' | 'host'>): string {
  const protocol = locationLike.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${locationLike.host}/ws/agent`;
}

export function parseAgentConsoleFrame(raw: unknown): AgentConsoleFrame | null {
  let value: unknown = raw;
  if (typeof value === 'string') {
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.type !== 'string' || !FRAME_TYPES.has(candidate.type as AgentConsoleFrame['type'])) {
    return null;
  }
  if (candidate.type !== 'ready' && candidate.type !== 'error') {
    if (typeof candidate.runId !== 'string' || !candidate.runId) return null;
    if (typeof candidate.sequence !== 'number' || !Number.isSafeInteger(candidate.sequence) || candidate.sequence < 1) {
      return null;
    }
  }
  if ('message' in candidate && typeof candidate.message !== 'string') return null;
  return candidate as unknown as AgentConsoleFrame;
}

export function nextAgentConsoleState(
  current: AgentConsoleState,
  frame: AgentConsoleFrame,
): AgentConsoleState {
  if (frame.type === 'completed') return 'completed';
  if (frame.type === 'cancelled') return 'cancelled';
  if (frame.type === 'limit_reached') return 'step_limit';
  if (frame.status === 'cancelling') return 'cancelling';
  if (frame.type === 'error') {
    return frame.code === 'authentication_required' ? 'unauthorized' : 'failed';
  }
  if (frame.type === 'run_started' || frame.type === 'status' || frame.type === 'terminal') {
    return current === 'cancelling' ? 'cancelling' : 'running';
  }
  return current;
}

export function isRunAcknowledgementFrame(frame: AgentConsoleFrame): boolean {
  return frame.type !== 'ready'
    && typeof frame.runId === 'string'
    && frame.runId.length > 0
    && Number.isInteger(frame.sequence);
}

export function reconnectingAgentConsoleState(cancelRequested: boolean): AgentConsoleState {
  return cancelRequested ? 'cancelling' : 'disconnected';
}

export function shouldResendAgentCancellation(
  mode: 'start' | 'attach',
  cancelRequested: boolean,
  runId?: string,
): boolean {
  return mode === 'attach' && cancelRequested && Boolean(runId);
}

export function sanitizeTerminalChunk(value: string, limit = 12_000): string {
  const withoutControls = value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]/g, '');
  return withoutControls.length > limit
    ? `${withoutControls.slice(0, limit)}\n...[truncated]`
    : withoutControls;
}
