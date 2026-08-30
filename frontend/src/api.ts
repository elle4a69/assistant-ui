const getApiBase = () => {
  if (typeof window !== 'undefined') {
    // The native booking bundle can be embedded on a different website. In
    // that case its API must remain the booking application's API, rather than
    // resolving requests against the host website's origin.
    const bookingContainer = document.getElementById('booking-container');
    const configuredApiBase = bookingContainer?.dataset.apiBase?.trim();
    if (configuredApiBase) {
      return configuredApiBase.replace(/\/+$/, '');
    }

    const isLocalBrowser = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    // A production build must never call localhost on the viewer's computer,
    // even if a developer's VITE_API_BASE was present when the bundle was built.
    if (!isLocalBrowser) {
      return window.location.origin;
    }
    if (import.meta.env.VITE_API_BASE && import.meta.env.VITE_API_BASE.trim() !== '') {
      return import.meta.env.VITE_API_BASE;
    }
    return `${window.location.protocol}//${window.location.hostname}:8025`;
  }
  if (import.meta.env.VITE_API_BASE && import.meta.env.VITE_API_BASE.trim() !== '') {
    return import.meta.env.VITE_API_BASE;
  }
  return 'http://localhost:8025';
};

const API_BASE = getApiBase();

const nativeFetch = globalThis.fetch.bind(globalThis);

async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const response = await nativeFetch(input, init);
  const url = typeof input === 'string' ? input : input.toString();
  if (response.status === 401 && !url.includes('/api/auth/login')) {
    window.dispatchEvent(new Event('admin-auth-required'));
  }
  return response;
}

export async function getAdminAuthStatus(): Promise<{ authenticated: boolean }> {
  const response = await apiFetch(`${API_BASE}/api/auth/status`, { credentials: 'same-origin' });
  if (!response.ok) return { authenticated: false };
  return response.json();
}

export async function loginAdmin(username: string, password: string): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/auth/login`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || 'Login failed.');
  }
}

export async function logoutAdmin(): Promise<void> {
  await apiFetch(`${API_BASE}/api/auth/logout`, { method: 'POST', credentials: 'same-origin' });
}


export interface ThreadSLA {
  dueAt: string;
  level: string;
}

export interface ThreadListItem {
  id: string;
  customerPhone: string;
  smsAccountKey: 'primary' | 'secondary';
  lastMessageAt: string;
  lastMessageText: string;
  lastMessageRole: Message['role'] | null;
  lastArrivalAt: string | null;
  lastArrivalEventId: string | null;
  lastArrivalSessionId: string | null;
  pendingArrivalSessionId: string | null;
  pendingArrivalEventId: string | null;
  pendingArrivalAt: string | null;
  unreadCount: number;
  priority: string;
  status: string;
  assignedAgentName: string | null;
  assignedAgentId: string | null;
  sla: ThreadSLA;
  autoReplyEnabled: boolean;
}

export interface Message {
  id: string;
  role: 'customer' | 'agent' | 'system' | 'draft';
  text: string;
  at: string;
}

export interface Note {
  id: string;
  agentId: string;
  text: string;
  at: string;
}

export interface ThreadEvent {
  id: string;
  type: string;
  agentId: string | null;
  at: string;
  meta: Record<string, any>;
}

export interface ThreadDetail {
  id: string;
  customerPhone: string;
  smsAccountKey: 'primary' | 'secondary';
  state: string;
  assignedAgent: { id: string; name: string } | null;
  sla: {
    dueAt: string;
    level: string;
    status: 'ok' | 'breaching' | 'breached';
  };
  messages: Message[];
  notes: Note[];
  events: ThreadEvent[];
  autoReplyEnabled: boolean;
  pendingArrivalSessionId: string | null;
  pendingArrivalEventId: string | null;
  pendingArrivalAt: string | null;
}

export interface CalendarBooking {
  id: string;
  customerPhone: string;
  summary: string;
  smsAccountKey?: 'primary' | 'secondary' | null;
  threadId?: string | null;
  startTime: string;
  endTime: string;
  status?: 'scheduled' | 'completed' | 'no_show' | 'cancelled';
  notes?: string;
}

export interface FreeBusySlot {
  startTime: string;
  endTime: string;
}

export interface BootcampPersona {
  id: string;
  name: string;
  category: string;
  description: string;
  prompt: string;
}

export type BootcampStyleProfile = Record<
  'flirtiness' | 'cheerfulness' | 'wit' | 'sarcasm' | 'warmth' | 'directness' | 'chattiness' | 'patience',
  number
>;

export interface BootcampMessage {
  id: string;
  role: 'persona' | 'tori';
  text: string;
  meta: Record<string, unknown>;
  createdAt: string;
}

export interface BootcampConversation {
  id: string;
  personaId: string;
  personaName: string;
  status: string;
  currentTurn: number;
  needsHandoff: boolean;
  handoffReason: string | null;
  messages: BootcampMessage[];
}

export interface BootcampRun {
  id: string;
  status: string;
  selectedPersonaIds: string[];
  maxTurns: number;
  styleProfile: BootcampStyleProfile;
  error: string | null;
  createdAt: string;
  updatedAt: string;
  conversations: BootcampConversation[];
}

export async function getBootcampPersonas(): Promise<BootcampPersona[]> {
  const response = await apiFetch(`${API_BASE}/api/bootcamp/personas`);
  if (!response.ok) throw new Error('Failed to load Boot Camp personas');
  return response.json();
}

export async function getBootcampProfile(): Promise<{
  active: BootcampStyleProfile;
  defaults: BootcampStyleProfile;
  isApplied: boolean;
  canUndo: boolean;
}> {
  const response = await apiFetch(`${API_BASE}/api/bootcamp/profile`);
  if (!response.ok) throw new Error('Failed to load Tori style profile');
  return response.json();
}

export async function applyBootcampProfile(styleProfile: BootcampStyleProfile) {
  const response = await apiFetch(`${API_BASE}/api/bootcamp/profile/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ styleProfile }),
  });
  if (!response.ok) throw new Error('Failed to apply profile');
  return response.json();
}

export async function undoBootcampProfile() {
  const response = await apiFetch(`${API_BASE}/api/bootcamp/profile/undo`, { method: 'POST' });
  if (!response.ok) throw new Error('Failed to restore previous profile');
  return response.json();
}

export async function startBootcampRun(
  personaIds: string[],
  maxTurns: number,
  styleProfile: BootcampStyleProfile,
): Promise<BootcampRun> {
  const response = await apiFetch(`${API_BASE}/api/bootcamp/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ personaIds, maxTurns, styleProfile }),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function getLatestBootcampRun(): Promise<BootcampRun | null> {
  const response = await apiFetch(`${API_BASE}/api/bootcamp/runs/latest`);
  if (!response.ok) throw new Error('Failed to load Boot Camp run');
  const payload = await response.json();
  return payload.run;
}

export async function controlBootcampRun(runId: string, operation: 'pause' | 'resume' | 'stop') {
  const response = await apiFetch(`${API_BASE}/api/bootcamp/runs/${runId}/control`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operation }),
  });
  if (!response.ok) throw new Error('Failed to control Boot Camp run');
  return response.json() as Promise<BootcampRun>;
}

export async function resetBootcampRuns() {
  const response = await apiFetch(`${API_BASE}/api/bootcamp/runs`, { method: 'DELETE' });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export interface ListThreadsParams {
  search?: string;
  filterStatus?: string;
  filterPriority?: string;
  onlyUnread?: boolean;
}

export async function listThreads(params: ListThreadsParams = {}): Promise<ThreadListItem[]> {
  const url = new URL(`${API_BASE}/api/threads`);
  if (params.search) url.searchParams.append('search', params.search);
  if (params.filterStatus) url.searchParams.append('filterStatus', params.filterStatus);
  if (params.filterPriority) url.searchParams.append('filterPriority', params.filterPriority);
  if (params.onlyUnread !== undefined) url.searchParams.append('onlyUnread', String(params.onlyUnread));

  const response = await apiFetch(url.toString(), { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to list threads: ${response.statusText}`);
  }
  return response.json();
}

export interface CatchUpResult {
  processed: boolean;
  threadId?: string;
  outcome: 'sent' | 'information-request' | 'complete';
  remaining: number;
}

export interface ArrivalMessage {
  id: string;
  sender: 'client' | 'provider' | 'system';
  text: string;
  createdAt: string;
}

export interface ArrivalSession {
  id: string;
  bookingId: string;
  threadId: string | null;
  smsAccountKey: 'primary' | 'secondary' | null;
  arrivalEventId: string | null;
  status: 'invited' | 'active' | 'closed' | 'expired';
  expiresAt: string;
  activatedAt: string | null;
  acknowledgedAt: string | null;
  lastAlertAt: string | null;
  nextAlertAt: string | null;
  alertCount: number;
  closedAt: string | null;
  lastActivityAt: string;
  booking: {
    summary: string;
    customerPhone: string | null;
    startTime: string | null;
    endTime: string | null;
  };
  messages?: ArrivalMessage[];
}

export async function catchUpMissedMessage(): Promise<CatchUpResult> {
  const response = await apiFetch(`${API_BASE}/api/threads/catch-up`, { method: 'POST' });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || 'Failed to catch up missed messages');
  }
  return response.json();
}

export async function getThread(id: string): Promise<ThreadDetail> {
  const response = await apiFetch(`${API_BASE}/api/threads/${id}`, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to get thread detail: ${response.statusText}`);
  }
  return response.json();
}

export async function takeOverThread(id: string, agentId: string): Promise<{ status: string; state: string; assignedAgentId: string }> {
  const response = await apiFetch(`${API_BASE}/api/threads/${id}/takeover`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agentId }),
  });
  if (!response.ok) {
    throw new Error(`Failed to takeover thread: ${response.statusText}`);
  }
  return response.json();
}

export async function sendThreadReply(
  id: string,
  agentId: string,
  text: string,
  clientRequestId: string,
): Promise<Message> {
  const response = await apiFetch(`${API_BASE}/api/threads/${id}/reply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agentId, text, clientRequestId }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Failed to send reply: ${response.statusText}`);
  }
  return response.json();
}

export async function respondToBootcampInformationRequest(
  conversationId: string,
  information: string,
): Promise<{
  status: string;
  conversation: BootcampConversation;
  knowledgeSource: string;
  knowledgeSummary: string;
}> {
  const response = await apiFetch(`${API_BASE}/api/bootcamp/conversations/${conversationId}/information-request/respond`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ information }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || 'Tori could not use that information. Nothing was saved.');
  }
  return response.json();
}

export interface InformationRequestResult {
  status: string;
  message: Message;
  knowledgeSource: string;
  knowledgeSummary: string;
}

export async function respondToInformationRequest(
  threadId: string,
  requestEventId: string,
  agentId: string,
  information: string,
): Promise<InformationRequestResult> {
  const response = await apiFetch(`${API_BASE}/api/threads/${threadId}/information-request/respond`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ requestEventId, agentId, information }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || 'The information could not be saved and no reply was sent.');
  }
  return response.json();
}

export async function addThreadNote(id: string, agentId: string, text: string): Promise<Note> {
  const response = await apiFetch(`${API_BASE}/api/threads/${id}/notes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agentId, text }),
  });
  if (!response.ok) {
    throw new Error(`Failed to add note: ${response.statusText}`);
  }
  return response.json();
}

export async function escalateThread(id: string, agentId: string, reason: string): Promise<{ status: string; state: string }> {
  const response = await apiFetch(`${API_BASE}/api/threads/${id}/escalate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agentId, reason }),
  });
  if (!response.ok) {
    throw new Error(`Failed to escalate thread: ${response.statusText}`);
  }
  return response.json();
}

export async function resolveThread(id: string, agentId: string, resolution: string): Promise<{ status: string; state: string }> {
  const response = await apiFetch(`${API_BASE}/api/threads/${id}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agentId, resolution }),
  });
  if (!response.ok) {
    throw new Error(`Failed to resolve thread: ${response.statusText}`);
  }
  return response.json();
}

export async function toggleAutoresponder(threadId: string, enabled: boolean): Promise<{ status: string; autoReplyEnabled: boolean }> {
  const response = await apiFetch(`${API_BASE}/api/threads/${threadId}/autoresponder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) {
    throw new Error(`Failed to toggle autoresponder: ${response.statusText}`);
  }
  return response.json();
}

export async function sendCustomerSms(
  customerPhone: string,
  body: string,
  smsAccountKey: 'primary' | 'secondary',
): Promise<{ status: string; thread_id: string; customer_phone: string; sms_account_key: 'primary' | 'secondary'; provider_sends: 0 }> {
  const response = await apiFetch(`${API_BASE}/api/admin/sms-simulator`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      customer_phone: customerPhone,
      body,
      sms_account_key: smsAccountKey,
    }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = typeof payload?.detail === 'string'
      ? payload.detail
      : Array.isArray(payload?.detail)
        ? payload.detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join('; ')
        : '';
    throw new Error(detail || `SMS simulation failed (${response.status}).`);
  }
  return response.json();
}

export async function listBookings(options: { includePast?: boolean } = {}): Promise<CalendarBooking[]> {
  const query = options.includePast ? '?includePast=true' : '';
  const response = await apiFetch(`${API_BASE}/api/calendar/bookings${query}`);
  if (!response.ok) {
    throw new Error(`Failed to list calendar bookings: ${response.statusText}`);
  }
  return response.json();
}



export async function updateBooking(id: string, payload: Partial<CalendarBooking>): Promise<CalendarBooking> {
  const response = await apiFetch(`${API_BASE}/api/calendar/bookings/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Failed to update booking: ${response.statusText}`);
  }
  return response.json();
}

export async function deleteBooking(id: string): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/calendar/bookings/${id}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    throw new Error(`Failed to delete booking: ${response.statusText}`);
  }
  return response.json();
}


export async function getFreeBusy(duration?: number): Promise<FreeBusySlot[]> {
  const url = new URL(`${API_BASE}/api/calendar/freebusy`);
  if (duration !== undefined) {
    url.searchParams.append('duration', String(duration));
  }
  const response = await apiFetch(url.toString());
  if (!response.ok) {
    throw new Error(`Failed to get free/busy calendar slots: ${response.statusText}`);
  }
  return response.json();
}

export interface SystemSettings {
  openaiApiKey: string;
  systemPrompt: string;
  userPrompt: string;
  hasGoogleCredentials: boolean;
  autoReplyGlobalEnabled?: boolean;
  trainingModeEnabled?: boolean;
  showMessageAvatars?: boolean;
  catchUpLookbackDays?: number;
}

export interface KnowledgeFile {
  name: string;
  sizeBytes: number;
}

export async function createArrivalInvite(
  booking: CalendarBooking,
  smsAccountKey: 'primary' | 'secondary',
  threadId?: string,
): Promise<{ session: ArrivalSession; link: string }> {
  const response = await apiFetch(`${API_BASE}/api/arrival/admin/bookings/${encodeURIComponent(booking.id)}/invite`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      summary: booking.summary,
      customerPhone: booking.customerPhone || null,
      smsAccountKey,
      threadId: threadId || null,
      startTime: booking.startTime,
      endTime: booking.endTime,
    }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Could not create arrival link.');
  const result = await response.json();
  if (typeof window !== 'undefined') {
    const generated = new URL(result.link, window.location.origin);
    result.link = generated.pathname.startsWith('/a/')
      ? `${window.location.origin}${generated.pathname}`
      : `${window.location.origin}/arrival${generated.hash}`;
  }
  return result;
}

export async function activateArrival(inviteToken: string): Promise<{ alreadyActivated: boolean; clientToken: string; session: ArrivalSession }> {
  const response = await apiFetch(`${API_BASE}/api/arrival/activate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ inviteToken }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'This arrival link is not valid.');
  return response.json();
}

export async function getArrivalInviteStatus(inviteToken: string): Promise<{
  active: boolean;
  clientToken: string | null;
  session: ArrivalSession | null;
}> {
  const response = await apiFetch(`${API_BASE}/api/arrival/status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ inviteToken }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'This arrival link is not valid.');
  return response.json();
}

export async function acknowledgeThreadArrival(threadId: string, sessionId: string): Promise<void> {
  const response = await apiFetch(
    `${API_BASE}/api/threads/${encodeURIComponent(threadId)}/arrivals/${encodeURIComponent(sessionId)}/acknowledge`,
    { method: 'POST' },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || 'Could not acknowledge the customer arrival.');
  }
}

export async function getClientArrivalSession(sessionId: string, token: string): Promise<ArrivalSession> {
  const response = await apiFetch(`${API_BASE}/api/arrival/client/${encodeURIComponent(sessionId)}`, {
    headers: { Authorization: `Arrival ${token}` },
  });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Arrival chat unavailable.');
  return response.json();
}

export async function sendClientArrivalMessage(sessionId: string, token: string, text: string): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/arrival/client/${encodeURIComponent(sessionId)}/messages`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Arrival ${token}` }, body: JSON.stringify({ text }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Message failed to send.');
}

export async function listArrivalSessions(): Promise<ArrivalSession[]> {
  const response = await apiFetch(`${API_BASE}/api/arrival/admin/sessions`);
  if (!response.ok) throw new Error('Could not load arrival chats.');
  return response.json();
}

export async function getAdminArrivalSession(sessionId: string): Promise<ArrivalSession> {
  const response = await apiFetch(`${API_BASE}/api/arrival/admin/sessions/${encodeURIComponent(sessionId)}`);
  if (!response.ok) throw new Error('Could not load arrival chat.');
  return response.json();
}

export async function sendAdminArrivalMessage(sessionId: string, text: string): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/arrival/admin/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Message failed to send.');
}

export async function closeArrivalSession(sessionId: string): Promise<ArrivalSession> {
  const response = await apiFetch(`${API_BASE}/api/arrival/admin/sessions/${encodeURIComponent(sessionId)}/close`, { method: 'POST' });
  if (!response.ok) throw new Error('Could not close arrival chat.');
  return response.json();
}

export interface PushConfig {
  supported: boolean;
  configured: boolean;
  publicKey: string;
  activeSubscriptions: number;
}

export async function getPushConfig(): Promise<PushConfig> {
  const response = await apiFetch(`${API_BASE}/api/push/config`, { cache: 'no-store' });
  if (!response.ok) throw new Error('Could not load push notification settings.');
  return response.json();
}

export async function savePushSubscription(subscription: PushSubscriptionJSON): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/push/subscriptions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(subscription),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Could not enable push alerts.');
}

export async function deletePushSubscription(subscription: PushSubscriptionJSON): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/push/subscriptions`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(subscription),
  });
  if (!response.ok) throw new Error('Could not disable push alerts.');
}

export interface ManualLearningEntry {
  id: string;
  type: 'manual_guidance';
  topic: string;
  applies_when: string;
  instruction: string;
  example_reply: string;
  owner_topic: string;
  owner_guidance: string;
  text: string;
  created_at: string;
  updated_at: string;
  scope?: 'shared' | 'primary' | 'secondary';
}

export interface SmsLineProfile {
  displayName: string;
  providerName: string;
  informationUrl: string;
  userPrompt: string;
}

export async function getSmsLineProfiles(): Promise<Record<'primary' | 'secondary', SmsLineProfile>> {
  const response = await apiFetch(`${API_BASE}/api/settings/line-profiles`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Failed to load line settings: ${response.statusText}`);
  return (await response.json()).profiles;
}

export async function saveSmsLineProfiles(profiles: Record<'primary' | 'secondary', SmsLineProfile>): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/settings/line-profiles`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profiles),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail || `Failed to save line settings: ${response.statusText}`);
  }
}

export interface LearnedInformationEntry extends Omit<ManualLearningEntry, 'scope'> {
  scope: 'shared' | 'primary' | 'secondary' | 'internal';
  review_status?: 'pending' | 'approved';
  retrieval_enabled?: boolean;
  category?: string;
  review_note?: string;
  review_source?: 'ai-drafted' | 'ai-redrafted' | 'staff-edited-reply' | 'sms-pair-template';
}

export interface SmsLearningPreviewItem {
  id: string;
  account_key: 'primary' | 'secondary';
  customer: string;
  reply: string;
  reason: string;
  topic?: string;
  applies_when?: string;
  instruction?: string;
  example_reply?: string;
}

export interface SmsLearningPreview {
  sampled: number;
  candidates: SmsLearningPreviewItem[];
  rejected: SmsLearningPreviewItem[];
}

export async function getSettings(): Promise<SystemSettings> {
  const response = await apiFetch(`${API_BASE}/api/settings`, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to get settings: ${response.statusText}`);
  }
  return response.json();
}

export async function updateSettings(settings: { openaiApiKey?: string; systemPrompt?: string; userPrompt?: string; autoReplyGlobalEnabled?: boolean; trainingModeEnabled?: boolean; showMessageAvatars?: boolean; catchUpLookbackDays?: number }): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  if (!response.ok) {
    throw new Error(`Failed to update settings: ${response.statusText}`);
  }
  const result = await response.json();
  if (typeof settings.showMessageAvatars === 'boolean') {
    window.dispatchEvent(new CustomEvent('message-avatar-setting-changed', {
      detail: { showMessageAvatars: settings.showMessageAvatars },
    }));
  }
  return result;
}

export interface BusinessVariable {
  key: string;
  token?: string;
  label: string;
  value: string;
  description?: string;
  required?: boolean;
  required_status?: string;
}

export interface RagStatus {
  enabled: boolean;
  feature_flag_enabled: boolean;
  rag_state: string;
  dataset_path?: string;
  validation_status: string;
  validation_error?: string | null;
  total_examples: number;
  intent_counts: Record<string, number>;
  dataset_hash?: string | null;
  last_validated_at?: string | null;
}

export async function getRagStatus(): Promise<RagStatus> {
  const response = await apiFetch(`${API_BASE}/api/admin/rag/status`);
  if (!response.ok) {
    throw new Error(`Failed to get RAG status: ${response.statusText}`);
  }
  return response.json();
}

export async function getBusinessVariables(): Promise<BusinessVariable[]> {
  const response = await apiFetch(`${API_BASE}/api/settings/business-variables`);
  if (!response.ok) {
    throw new Error(`Failed to get business variables: ${response.statusText}`);
  }
  const result: { variables: BusinessVariable[] } = await response.json();
  return result.variables;
}

export async function saveBusinessVariables(variables: BusinessVariable[]): Promise<{ status: string; variables: BusinessVariable[] }> {
  const response = await apiFetch(`${API_BASE}/api/settings/business-variables`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ variables }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail || `Failed to save business variables: ${response.statusText}`);
  }
  return response.json();
}

export async function listKnowledgeFiles(): Promise<KnowledgeFile[]> {
  const response = await apiFetch(`${API_BASE}/api/settings/knowledge-files`);
  if (!response.ok) {
    throw new Error(`Failed to list knowledge files: ${response.statusText}`);
  }
  return response.json();
}

export async function createManualLearning(
  topic: string,
  guidance: string,
  scope: 'shared' | 'primary' | 'secondary',
): Promise<{ status: string; filename: string; entry: ManualLearningEntry }> {
  const response = await apiFetch(`${API_BASE}/api/settings/learnings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ topic, guidance, scope }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || 'The learning could not be structured. Nothing was saved.');
  }
  return response.json();
}

export async function listLearnedInformation(): Promise<LearnedInformationEntry[]> {
  const response = await apiFetch(`${API_BASE}/api/settings/learnings`);
  if (!response.ok) throw new Error('Failed to load learned rules.');
  return (await response.json()).entries;
}

export async function updateLearnedInformation(entry: LearnedInformationEntry): Promise<LearnedInformationEntry> {
  const response = await apiFetch(`${API_BASE}/api/settings/learnings/${encodeURIComponent(entry.id)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ topic: entry.topic || '', text: entry.text, scope: entry.scope }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Failed to save learned rule.');
  return (await response.json()).entry;
}

export async function approveLearnedInformation(id: string): Promise<LearnedInformationEntry> {
  const response = await apiFetch(`${API_BASE}/api/settings/learnings/${encodeURIComponent(id)}/approve`, { method: 'POST' });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Failed to approve learned rule.');
  return (await response.json()).entry;
}

export async function approvePendingLearnedInformation(): Promise<{ processed: number; active: number; restricted: number }> {
  const response = await apiFetch(`${API_BASE}/api/settings/learnings/approve-pending`, { method: 'POST' });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Failed to approve pending learned rules.');
  return response.json();
}

export async function approveSelectedLearnedInformation(entryIds: string[]): Promise<{ processed: number; active: number; restricted: number }> {
  const response = await apiFetch(`${API_BASE}/api/settings/learnings/approve-selected`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entry_ids: entryIds }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Failed to approve selected learned rules.');
  return response.json();
}

export async function previewSmsPairLearnings(limit = 50): Promise<SmsLearningPreview> {
  const response = await apiFetch(`${API_BASE}/api/settings/learnings/sms-pair-preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ limit }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Failed to generate the SMS training preview.');
  return response.json();
}

export async function importSmsPairLearningCandidates(
  candidates: SmsLearningPreviewItem[],
): Promise<{ status: string; created: number; skipped: number }> {
  const response = await apiFetch(`${API_BASE}/api/settings/learnings/sms-pair-import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ candidates: candidates.map(({ id, account_key, topic, applies_when, instruction, example_reply }) => ({
      id,
      account_key,
      topic: topic || '',
      applies_when: applies_when || '',
      instruction: instruction || '',
      example_reply: example_reply || '',
    })) }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Failed to add SMS learning candidates to review.');
  return response.json();
}

export async function redraftLearnedInformation(id: string): Promise<LearnedInformationEntry> {
  const response = await apiFetch(`${API_BASE}/api/settings/learnings/${encodeURIComponent(id)}/redraft`, { method: 'POST' });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Failed to redraft learned rule.');
  return (await response.json()).entry;
}

export async function redraftPendingLearnedInformation(): Promise<{ processed: number; failed: number }> {
  const response = await apiFetch(`${API_BASE}/api/settings/learnings/redraft-pending`, { method: 'POST' });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Failed to redraft pending learned rules.');
  return response.json();
}

export async function moveAllLearnedInformationToReview(): Promise<number> {
  const response = await apiFetch(`${API_BASE}/api/settings/learnings/move-all-to-review`, { method: 'POST' });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Failed to move learned rules to review.');
  return (await response.json()).moved;
}

export async function deleteLearnedInformation(id: string): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/settings/learnings/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Failed to delete learned rule.');
}

export async function uploadKnowledgeFile(file: File): Promise<{ status: string; filename: string }> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await apiFetch(`${API_BASE}/api/settings/upload-knowledge`, {
    method: 'POST',
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`Failed to upload knowledge file: ${response.statusText}`);
  }
  return response.json();
}

export async function uploadCredentialsFile(file: File): Promise<{ status: string }> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await apiFetch(`${API_BASE}/api/settings/upload-credentials`, {
    method: 'POST',
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`Failed to upload credentials file: ${response.statusText}`);
  }
  return response.json();
}

export interface FileContentResponse {
  content: string;
}

export interface SearchResultItem {
  index: number;
  input: string;
  output: string;
}

export interface SearchFileResponse {
  results: SearchResultItem[];
  totalMatches: number;
}

export async function getKnowledgeFile(filename: string): Promise<FileContentResponse> {
  const response = await apiFetch(`${API_BASE}/api/settings/knowledge-files/${filename}`);
  if (!response.ok) {
    throw new Error(`Failed to read file content: ${response.statusText}`);
  }
  return response.json();
}

export async function saveKnowledgeFile(filename: string, content: string): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/settings/knowledge-files/${filename}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) {
    throw new Error(`Failed to save file content: ${response.statusText}`);
  }
  return response.json();
}

export async function deleteKnowledgeFile(filename: string): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/settings/knowledge-files/${filename}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    throw new Error(`Failed to delete file: ${response.statusText}`);
  }
  return response.json();
}

export async function searchKnowledgeFile(filename: string, query: string): Promise<SearchFileResponse> {
  const response = await apiFetch(`${API_BASE}/api/settings/knowledge-files/${filename}/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  });
  if (!response.ok) {
    throw new Error(`Failed to search file: ${response.statusText}`);
  }
  return response.json();
}

export async function purgeKnowledgeFile(filename: string, query?: string, indices?: number[]): Promise<{ status: string; purgedCount: number }> {
  const response = await apiFetch(`${API_BASE}/api/settings/knowledge-files/${filename}/purge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, indices }),
  });
  if (!response.ok) {
    throw new Error(`Failed to purge file content: ${response.statusText}`);
  }
  return response.json();
}

export interface Service {
  id: string;
  name: string;
  description: string;
  price: number;
  duration: number;
  showDuration?: boolean;
  /** The SMS line whose AI may use this service. */
  lineKey?: 'primary' | 'secondary';
}


export interface BookingPayload {
  serviceId: string;
  name: string;
  phone: string;
  startTime: string;
  notes?: string;
  providerKey?: 'tori' | 'anonymous';
}

export async function getServices(): Promise<Service[]> {
  const response = await apiFetch(`${API_BASE}/api/services`);
  if (!response.ok) {
    throw new Error(`Failed to fetch services: ${response.statusText}`);
  }
  return response.json();
}

export async function saveServices(services: Service[]): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/services`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ services }),
  });
  if (!response.ok) {
    throw new Error(`Failed to save services: ${response.statusText}`);
  }
  return response.json();
}

export async function getSmsTemplate(): Promise<{ template: string }> {
  const response = await apiFetch(`${API_BASE}/api/settings/sms-confirmation`);
  if (!response.ok) {
    throw new Error(`Failed to fetch SMS template: ${response.statusText}`);
  }
  return response.json();
}

export async function saveSmsTemplate(template: string): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/settings/sms-confirmation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ template }),
  });
  if (!response.ok) {
    throw new Error(`Failed to save SMS template: ${response.statusText}`);
  }
  return response.json();
}

export interface BookingReminderConfig {
  enabled: boolean;
  minutesBefore: number;
  template: string;
}

export async function getBookingReminderConfig(): Promise<BookingReminderConfig> {
  const response = await apiFetch(`${API_BASE}/api/settings/booking-reminder`, { cache: 'no-store' });
  if (!response.ok) throw new Error('Failed to load booking reminder settings');
  return response.json();
}

export async function saveBookingReminderConfig(config: BookingReminderConfig): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/settings/booking-reminder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  if (!response.ok) throw new Error('Failed to save booking reminder settings');
  return response.json();
}

export async function createBooking(booking: BookingPayload): Promise<{ status: string; smsSent: string; smsError?: string | null }> {
  const response = await apiFetch(`${API_BASE}/api/calendar/bookings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(booking),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Failed to create manual booking: ${response.statusText}`);
  }
  return response.json();
}


export interface WorkingHourEntry {
  day: string;
  enabled: boolean;
  open: string;
  close: string;
}

export async function getWorkingHours(): Promise<WorkingHourEntry[]> {
  const response = await apiFetch(`${API_BASE}/api/settings/working-hours`);
  if (!response.ok) throw new Error(`Failed to fetch working hours: ${response.statusText}`);
  return response.json();
}

export async function saveWorkingHours(hours: WorkingHourEntry[]): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/settings/working-hours`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hours }),
  });
  if (!response.ok) throw new Error(`Failed to save working hours: ${response.statusText}`);
  return response.json();
}

export interface MobileMessageConfig {
  username: string;
  password: string;
  hasPassword?: boolean;
  sender?: string;
  enabled: boolean;
}

export async function getMobileMessageConfig(): Promise<MobileMessageConfig> {
  const response = await apiFetch(`${API_BASE}/api/settings/mobilemessage`);
  if (!response.ok) throw new Error(`Failed to fetch MobileMessage settings: ${response.statusText}`);
  return response.json();
}

export async function saveMobileMessageConfig(config: MobileMessageConfig): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/settings/mobilemessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  if (!response.ok) throw new Error(`Failed to save MobileMessage settings: ${response.statusText}`);
  return response.json();
}

export interface QARule {
  id: string;
  trigger: string;
  reply: string;
}

export async function getQARules(): Promise<QARule[]> {
  const response = await apiFetch(`${API_BASE}/api/qa-rules`);
  if (!response.ok) throw new Error(`Failed to fetch QA rules: ${response.statusText}`);
  return response.json();
}

export async function saveQARules(rules: QARule[]): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/qa-rules`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(rules),
  });
  if (!response.ok) throw new Error(`Failed to save QA rules: ${response.statusText}`);
  return response.json();
}

export interface FirstContactAutoresponderConfig {
  enabled: boolean;
  cooldownDays: number;
  delaySeconds: number;
  message: string;
}

export interface FirstContactAutoresponderSettings {
  accounts: {
    primary: FirstContactAutoresponderConfig;
    secondary: FirstContactAutoresponderConfig;
  };
  labels: {
    primary: string;
    secondary: string;
  };
}

export async function getFirstContactAutoresponder(): Promise<FirstContactAutoresponderSettings> {
  const response = await apiFetch(`${API_BASE}/api/settings/first-contact-autoresponder`);
  if (!response.ok) throw new Error(`Failed to fetch first-contact auto-responder: ${response.statusText}`);
  return response.json();
}

export async function saveFirstContactAutoresponder(
  config: FirstContactAutoresponderSettings
): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/settings/first-contact-autoresponder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ accounts: config.accounts }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Failed to save first-contact auto-responder: ${response.statusText}`);
  }
  return response.json();
}

export async function approveDraft(messageId: string): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/messages/${messageId}/approve`, {
    method: 'POST',
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Failed to approve draft message: ${response.statusText}`);
  }
  return response.json();
}

export async function updateDraft(messageId: string, text: string): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/messages/${messageId}/draft`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Failed to update draft message: ${response.statusText}`);
  }
  return response.json();
}

export async function discardDraft(messageId: string): Promise<{ status: string }> {
  const response = await apiFetch(`${API_BASE}/api/messages/${messageId}/discard`, {
    method: 'POST',
  });
  if (!response.ok) throw new Error(`Failed to discard draft message: ${response.statusText}`);
  return response.json();
}

export interface ClearPendingDraftsResult {
  status: string;
  removedDrafts: number;
  affectedThreads: number;
}

export async function clearPendingDrafts(): Promise<ClearPendingDraftsResult> {
  const response = await apiFetch(`${API_BASE}/api/messages/drafts/pending`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Failed to clear pending drafts: ${response.statusText}`);
  }
  return response.json();
}

export interface ClearReviewOnlyThreadsResult {
  status: string;
  clearedThreads: number;
  draftReviewThreads: number;
}

export async function clearReviewOnlyThreads(): Promise<ClearReviewOnlyThreadsResult> {
  const response = await apiFetch(`${API_BASE}/api/messages/review/pending`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Failed to clear review tags: ${response.statusText}`);
  }
  return response.json();
}

export interface OperationsChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: string;
}

export interface OperationsChatCapabilities {
  readOnly: boolean;
  liveSnapshot: boolean;
  codeAccess: boolean;
  logAccess: boolean;
  diagnosticTools: boolean;
  messageSelfDiagnosis: boolean;
  webSearch: boolean;
  persistentMemory: boolean;
  controlledActions: boolean;
  requiresConfirmation: boolean;
}

export async function getOperationsChatMessages(): Promise<{ messages: OperationsChatMessage[] }> {
  const response = await apiFetch(`${API_BASE}/api/settings/operations-chat/messages`, {
    cache: 'no-store',
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Failed to load operations chat: ${response.statusText}`);
  }
  return response.json();
}

export async function sendOperationsChatMessage(message: string): Promise<{
  userMessage: OperationsChatMessage;
  assistantMessage: OperationsChatMessage;
  capabilities: OperationsChatCapabilities;
}> {
  const response = await apiFetch(`${API_BASE}/api/settings/operations-chat/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Operations AI could not answer: ${response.statusText}`);
  }
  return response.json();
}

export async function createOperationsRealtimeSession(sdp: string): Promise<string> {
  const response = await apiFetch(`${API_BASE}/api/settings/operations-chat/realtime`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/sdp' },
    body: sdp,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Realtime voice could not start: ${response.statusText}`);
  }
  return response.text();
}

export interface OperationsRealtimeTurn {
  sessionId: string;
  userItemId: string;
  responseId: string;
  userTranscript: string;
  assistantTranscript: string;
}

export async function persistOperationsRealtimeTurn(turn: OperationsRealtimeTurn): Promise<{
  persisted: boolean;
  messages: OperationsChatMessage[];
}> {
  const response = await apiFetch(`${API_BASE}/api/settings/operations-chat/realtime/turns`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(turn),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Voice conversation could not be saved: ${response.statusText}`);
  }
  return response.json();
}

export async function runOperationsRealtimeTool(
  name: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const response = await apiFetch(`${API_BASE}/api/settings/operations-chat/realtime/tool`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, arguments: args }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Voice diagnostic failed: ${response.statusText}`);
  }
  return response.json();
}

export interface AgentConsoleRun {
  id: string;
  requestId: string;
  objective: string;
  status: 'starting' | 'running' | 'completed' | 'cancelled' | 'failed' | 'step_limit' | 'interrupted';
  stepCount: number;
  maxSteps: number;
  cancelRequested: boolean;
  finalSummary: string | null;
  error: string | null;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
}

export async function listAgentConsoleRuns(limit = 12): Promise<{
  enabled: boolean;
  runs: AgentConsoleRun[];
}> {
  const response = await apiFetch(`${API_BASE}/api/settings/agent-console/runs?limit=${Math.max(1, Math.min(50, limit))}`, {
    cache: 'no-store',
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Could not load Operations Console runs: ${response.statusText}`);
  }
  return response.json();
}


