const getApiBase = () => {
  if (typeof window !== 'undefined') {
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


export interface ThreadSLA {
  dueAt: string;
  level: string;
}

export interface ThreadListItem {
  id: string;
  customerPhone: string;
  lastMessageAt: string;
  lastMessageText: string;
  lastMessageRole: Message['role'] | null;
  lastArrivalAt: string | null;
  lastArrivalEventId: string | null;
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
}

export interface CalendarBooking {
  id: string;
  customerPhone: string;
  summary: string;
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
  const response = await fetch(`${API_BASE}/api/bootcamp/personas`);
  if (!response.ok) throw new Error('Failed to load Boot Camp personas');
  return response.json();
}

export async function getBootcampProfile(): Promise<{
  active: BootcampStyleProfile;
  defaults: BootcampStyleProfile;
  isApplied: boolean;
  canUndo: boolean;
}> {
  const response = await fetch(`${API_BASE}/api/bootcamp/profile`);
  if (!response.ok) throw new Error('Failed to load Tori style profile');
  return response.json();
}

export async function applyBootcampProfile(styleProfile: BootcampStyleProfile) {
  const response = await fetch(`${API_BASE}/api/bootcamp/profile/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ styleProfile }),
  });
  if (!response.ok) throw new Error('Failed to apply profile');
  return response.json();
}

export async function undoBootcampProfile() {
  const response = await fetch(`${API_BASE}/api/bootcamp/profile/undo`, { method: 'POST' });
  if (!response.ok) throw new Error('Failed to restore previous profile');
  return response.json();
}

export async function startBootcampRun(
  personaIds: string[],
  maxTurns: number,
  styleProfile: BootcampStyleProfile,
): Promise<BootcampRun> {
  const response = await fetch(`${API_BASE}/api/bootcamp/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ personaIds, maxTurns, styleProfile }),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function getLatestBootcampRun(): Promise<BootcampRun | null> {
  const response = await fetch(`${API_BASE}/api/bootcamp/runs/latest`);
  if (!response.ok) throw new Error('Failed to load Boot Camp run');
  const payload = await response.json();
  return payload.run;
}

export async function controlBootcampRun(runId: string, operation: 'pause' | 'resume' | 'stop') {
  const response = await fetch(`${API_BASE}/api/bootcamp/runs/${runId}/control`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operation }),
  });
  if (!response.ok) throw new Error('Failed to control Boot Camp run');
  return response.json() as Promise<BootcampRun>;
}

export async function resetBootcampRuns() {
  const response = await fetch(`${API_BASE}/api/bootcamp/runs`, { method: 'DELETE' });
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

  const response = await fetch(url.toString(), { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to list threads: ${response.statusText}`);
  }
  return response.json();
}

export interface CatchUpResult {
  processed: boolean;
  threadId?: string;
  outcome: 'draft' | 'information-request' | 'complete';
  remaining: number;
}

export async function catchUpMissedMessage(): Promise<CatchUpResult> {
  const response = await fetch(`${API_BASE}/api/threads/catch-up`, { method: 'POST' });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || 'Failed to catch up missed messages');
  }
  return response.json();
}

export async function getThread(id: string): Promise<ThreadDetail> {
  const response = await fetch(`${API_BASE}/api/threads/${id}`, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to get thread detail: ${response.statusText}`);
  }
  return response.json();
}

export async function takeOverThread(id: string, agentId: string): Promise<{ status: string; state: string; assignedAgentId: string }> {
  const response = await fetch(`${API_BASE}/api/threads/${id}/takeover`, {
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
  const response = await fetch(`${API_BASE}/api/threads/${id}/reply`, {
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
  const response = await fetch(`${API_BASE}/api/bootcamp/conversations/${conversationId}/information-request/respond`, {
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
  const response = await fetch(`${API_BASE}/api/threads/${threadId}/information-request/respond`, {
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
  const response = await fetch(`${API_BASE}/api/threads/${id}/notes`, {
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
  const response = await fetch(`${API_BASE}/api/threads/${id}/escalate`, {
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
  const response = await fetch(`${API_BASE}/api/threads/${id}/resolve`, {
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
  const response = await fetch(`${API_BASE}/api/threads/${threadId}/autoresponder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) {
    throw new Error(`Failed to toggle autoresponder: ${response.statusText}`);
  }
  return response.json();
}

export async function sendCustomerSms(fromPhone: string, body: string): Promise<{ status: string; thread_id: string }> {
  const response = await fetch(`${API_BASE}/webhooks/sms`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from: fromPhone,
      to: "+15557654321",
      body,
      receivedAt: new Date().toISOString(),
      isSimulation: true,
    }),
  });
  if (!response.ok) {
    throw new Error(`Failed to send customer SMS webhook: ${response.statusText}`);
  }
  return response.json();
}

export async function listBookings(): Promise<CalendarBooking[]> {
  const response = await fetch(`${API_BASE}/api/calendar/bookings`);
  if (!response.ok) {
    throw new Error(`Failed to list calendar bookings: ${response.statusText}`);
  }
  return response.json();
}



export async function updateBooking(id: string, payload: Partial<CalendarBooking>): Promise<CalendarBooking> {
  const response = await fetch(`${API_BASE}/api/calendar/bookings/${id}`, {
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
  const response = await fetch(`${API_BASE}/api/calendar/bookings/${id}`, {
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
  const response = await fetch(url.toString());
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
}

export interface KnowledgeFile {
  name: string;
  sizeBytes: number;
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
}

export async function getSettings(): Promise<SystemSettings> {
  const response = await fetch(`${API_BASE}/api/settings`, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to get settings: ${response.statusText}`);
  }
  return response.json();
}

export async function updateSettings(settings: { openaiApiKey?: string; systemPrompt?: string; userPrompt?: string; autoReplyGlobalEnabled?: boolean; trainingModeEnabled?: boolean; showMessageAvatars?: boolean }): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/settings`, {
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
  const response = await fetch(`${API_BASE}/api/admin/rag/status`);
  if (!response.ok) {
    throw new Error(`Failed to get RAG status: ${response.statusText}`);
  }
  return response.json();
}

export async function getBusinessVariables(): Promise<BusinessVariable[]> {
  const response = await fetch(`${API_BASE}/api/settings/business-variables`);
  if (!response.ok) {
    throw new Error(`Failed to get business variables: ${response.statusText}`);
  }
  const result: { variables: BusinessVariable[] } = await response.json();
  return result.variables;
}

export async function saveBusinessVariables(variables: BusinessVariable[]): Promise<{ status: string; variables: BusinessVariable[] }> {
  const response = await fetch(`${API_BASE}/api/settings/business-variables`, {
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
  const response = await fetch(`${API_BASE}/api/settings/knowledge-files`);
  if (!response.ok) {
    throw new Error(`Failed to list knowledge files: ${response.statusText}`);
  }
  return response.json();
}

export async function createManualLearning(
  topic: string,
  guidance: string,
): Promise<{ status: string; filename: string; entry: ManualLearningEntry }> {
  const response = await fetch(`${API_BASE}/api/settings/learnings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ topic, guidance }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || 'The learning could not be structured. Nothing was saved.');
  }
  return response.json();
}

export async function uploadKnowledgeFile(file: File): Promise<{ status: string; filename: string }> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE}/api/settings/upload-knowledge`, {
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

  const response = await fetch(`${API_BASE}/api/settings/upload-credentials`, {
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
  const response = await fetch(`${API_BASE}/api/settings/knowledge-files/${filename}`);
  if (!response.ok) {
    throw new Error(`Failed to read file content: ${response.statusText}`);
  }
  return response.json();
}

export async function saveKnowledgeFile(filename: string, content: string): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/settings/knowledge-files/${filename}`, {
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
  const response = await fetch(`${API_BASE}/api/settings/knowledge-files/${filename}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    throw new Error(`Failed to delete file: ${response.statusText}`);
  }
  return response.json();
}

export async function searchKnowledgeFile(filename: string, query: string): Promise<SearchFileResponse> {
  const response = await fetch(`${API_BASE}/api/settings/knowledge-files/${filename}/search`, {
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
  const response = await fetch(`${API_BASE}/api/settings/knowledge-files/${filename}/purge`, {
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
}


export interface BookingPayload {
  serviceId: string;
  name: string;
  phone: string;
  startTime: string;
  notes?: string;
}

export async function getServices(): Promise<Service[]> {
  const response = await fetch(`${API_BASE}/api/services`);
  if (!response.ok) {
    throw new Error(`Failed to fetch services: ${response.statusText}`);
  }
  return response.json();
}

export async function saveServices(services: Service[]): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/services`, {
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
  const response = await fetch(`${API_BASE}/api/settings/sms-confirmation`);
  if (!response.ok) {
    throw new Error(`Failed to fetch SMS template: ${response.statusText}`);
  }
  return response.json();
}

export async function saveSmsTemplate(template: string): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/settings/sms-confirmation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ template }),
  });
  if (!response.ok) {
    throw new Error(`Failed to save SMS template: ${response.statusText}`);
  }
  return response.json();
}

export async function createBooking(booking: BookingPayload): Promise<{ status: string; smsSent: string; smsError?: string | null }> {
  const response = await fetch(`${API_BASE}/api/calendar/bookings`, {
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
  const response = await fetch(`${API_BASE}/api/settings/working-hours`);
  if (!response.ok) throw new Error(`Failed to fetch working hours: ${response.statusText}`);
  return response.json();
}

export async function saveWorkingHours(hours: WorkingHourEntry[]): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/settings/working-hours`, {
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
  const response = await fetch(`${API_BASE}/api/settings/mobilemessage`);
  if (!response.ok) throw new Error(`Failed to fetch MobileMessage settings: ${response.statusText}`);
  return response.json();
}

export async function saveMobileMessageConfig(config: MobileMessageConfig): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/settings/mobilemessage`, {
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
  const response = await fetch(`${API_BASE}/api/qa-rules`);
  if (!response.ok) throw new Error(`Failed to fetch QA rules: ${response.statusText}`);
  return response.json();
}

export async function saveQARules(rules: QARule[]): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/qa-rules`, {
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

export async function getFirstContactAutoresponder(): Promise<FirstContactAutoresponderConfig> {
  const response = await fetch(`${API_BASE}/api/settings/first-contact-autoresponder`);
  if (!response.ok) throw new Error(`Failed to fetch first-contact auto-responder: ${response.statusText}`);
  return response.json();
}

export async function saveFirstContactAutoresponder(
  config: FirstContactAutoresponderConfig
): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/settings/first-contact-autoresponder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Failed to save first-contact auto-responder: ${response.statusText}`);
  }
  return response.json();
}

export async function approveDraft(messageId: string): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/messages/${messageId}/approve`, {
    method: 'POST',
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Failed to approve draft message: ${response.statusText}`);
  }
  return response.json();
}

export async function discardDraft(messageId: string): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/messages/${messageId}/discard`, {
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
  const response = await fetch(`${API_BASE}/api/messages/drafts/pending`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Failed to clear pending drafts: ${response.statusText}`);
  }
  return response.json();
}
