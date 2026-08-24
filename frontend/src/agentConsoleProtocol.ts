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


function readableLabel(value: string): string {
  const words = value
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!words) return 'Result';
  return `${words.charAt(0).toUpperCase()}${words.slice(1)}`;
}


function readableScalar(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'None';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return String(value);
}


function parseNestedJson(value: unknown): unknown {
  if (typeof value !== 'string') return value;
  const trimmed = value.trim();
  if (!(trimmed.startsWith('{') && trimmed.endsWith('}'))
    && !(trimmed.startsWith('[') && trimmed.endsWith(']'))) return value;
  try {
    const parsed = JSON.parse(trimmed);
    return parsed && typeof parsed === 'object' ? parsed : value;
  } catch {
    return value;
  }
}


function formatStructuredResult(value: unknown, indent = 0, depth = 0): string[] {
  const padding = ' '.repeat(indent);
  const parsedValue = depth < 6 ? parseNestedJson(value) : value;

  if (Array.isArray(parsedValue)) {
    if (parsedValue.length === 0) return [`${padding}None`];
    return parsedValue.flatMap((item, index) => {
      const parsedItem = depth < 6 ? parseNestedJson(item) : item;
      if (parsedItem !== null && typeof parsedItem === 'object') {
        return [`${padding}${index + 1}.`, ...formatStructuredResult(parsedItem, indent + 2, depth + 1)];
      }
      return [`${padding}• ${readableScalar(parsedItem)}`];
    });
  }

  if (parsedValue !== null && typeof parsedValue === 'object') {
    const entries = Object.entries(parsedValue as Record<string, unknown>);
    if (entries.length === 0) return [`${padding}None`];
    return entries.flatMap(([key, item]) => {
      const label = readableLabel(key);
      const parsedItem = depth < 6 ? parseNestedJson(item) : item;
      if (parsedItem !== null && typeof parsedItem === 'object') {
        return [`${padding}${label}:`, ...formatStructuredResult(parsedItem, indent + 2, depth + 1)];
      }
      const scalar = readableScalar(parsedItem);
      if (!scalar.includes('\n')) return [`${padding}${label}: ${scalar}`];
      return [
        `${padding}${label}:`,
        ...scalar.split('\n').map((line) => `${padding}  ${line}`),
      ];
    });
  }

  return [`${padding}${readableScalar(parsedValue)}`];
}


export function formatAgentTerminalMessage(message: string): string {
  const firstNewline = message.indexOf('\n');
  if (firstNewline < 0) return message;

  const rawCommand = message.slice(0, firstNewline).trim();
  const rawResult = message.slice(firstNewline + 1).trim();
  if (!rawCommand.startsWith('$ ') || !rawResult) return message;

  let parsed: unknown;
  try {
    parsed = JSON.parse(rawResult);
  } catch {
    return message;
  }
  if (parsed === null || typeof parsed !== 'object') return message;

  const command = rawCommand.slice(2).trim();
  const heading = command.startsWith('ops ')
    ? `Result: ${readableLabel(command.slice(4))}`
    : `Action: ${command}`;
  return [heading, ...formatStructuredResult(parsed)].join('\n');
}


export function sanitizeTerminalChunk(value: string, limit = 12_000): string {
  const withoutControls = value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]/g, '');
  return withoutControls.length > limit
    ? `${withoutControls.slice(0, limit)}\n...[truncated]`
    : withoutControls;
}
