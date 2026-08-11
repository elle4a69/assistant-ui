import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  listThreads,
  getThread,
  takeOverThread,
  sendThreadReply,
  addThreadNote,
  escalateThread,
  resolveThread,
  listBookings,
  getFreeBusy,
  approveDraft,
  discardDraft,
  respondToInformationRequest,
  deleteBooking,
  ThreadListItem,
  ThreadDetail,
  Message,
  CalendarBooking,
  FreeBusySlot
} from './api';
import { useExternalStoreRuntime, AssistantRuntimeProvider, ThreadPrimitive } from "@assistant-ui/react";
import {
  Search,
  CheckCircle2,
  AlertCircle,
  AlertTriangle,
  User,
  Clock,
  Send,
  FileText,
  Plus,
  Phone,
  Filter,
  Calendar,
  CalendarCheck,
  ArrowLeft,
  Trash2,
  X
} from 'lucide-react';

const CURRENT_AGENT_ID = 'agent-1';

// Custom thread viewer using ThreadPrimitive
function SmsAssistantThread({
  messages,
  events = [],
  onApproveDraft,
  onDiscardDraft
}: {
  messages: Message[];
  events?: any[];
  onApproveDraft?: (messageId: string) => void;
  onDiscardDraft?: (messageId: string) => void;
}) {
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, events]);

  // Combine and sort messages & events chronologically
  const timeline = useMemo(() => {
    const items: Array<{ type: 'message' | 'event'; at: string; data: any }> = [];
    messages.forEach(m => items.push({ type: 'message', at: m.at, data: m }));
    events.forEach(e => items.push({ type: 'event', at: e.at, data: e }));
    return items.sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime());
  }, [messages, events]);

  return (
    <ThreadPrimitive.Root className="flex flex-col flex-1 bg-slate-50 overflow-hidden">
      <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto p-4 flex flex-col gap-3 font-sans">
        {timeline.length === 0 ? (
          <div className="flex-1 flex flex-col justify-center items-center text-slate-400 py-12 text-center">
            <p className="text-xs font-semibold">No messages in this conversation yet</p>
          </div>
        ) : (
          timeline.map((item, idx) => {
            if (item.type === 'event') {
              const e = item.data;
              let label = `Event: ${e.type}`;
              if (e.type === 'takeover') label = `👤 Agent ${e.agentId || ''} took over conversation`;
              else if (e.type === 'auto-reply-sent') label = `🤖 Tori sent auto-reply`;
              else if (e.type === 'escalation') label = `⚠️ Escalated by ${e.agentId || ''}`;
              else if (e.type === 'resolution') label = `✅ Resolved by ${e.agentId || ''}`;

              return (
                <div key={`ev-${e.id || idx}`} className="self-center my-1 max-w-[85%] bg-slate-200/90 text-slate-600 text-[10px] font-semibold px-2.5 py-1 rounded-full border border-slate-300 flex items-center gap-1.5 shadow-2xs">
                  <span>{label}</span>
                  <span className="text-[9px] opacity-60 ml-1">
                    {e.at ? new Date(e.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
                  </span>
                </div>
              );
            }

            const m = item.data;
            const isCustomer = m.role === 'customer';
            const isAgent = m.role === 'agent';
            const isDraft = m.role === 'draft';

            return (
              <div
                key={`msg-${m.id || idx}`}
                className={`max-w-[85%] sm:max-w-[70%] p-3 text-xs shadow-sm rounded-2xl flex flex-col gap-1.5 ${
                  isCustomer
                    ? 'self-start bg-white text-slate-800 border border-slate-200 rounded-tl-xs'
                    : isAgent
                    ? 'self-end bg-purple-600 text-white rounded-tr-xs'
                    : isDraft
                    ? 'self-end bg-amber-50 border border-dashed border-amber-400 text-slate-800 rounded-tr-xs shadow-xs'
                    : 'self-end bg-indigo-600 text-white rounded-tr-xs'
                }`}
              >
                <div className={`flex items-center justify-between gap-3 text-[10px] font-bold border-b pb-1 ${isCustomer ? 'border-slate-100' : isDraft ? 'border-amber-200/50' : 'border-slate-100/30'}`}>
                  <span className={isCustomer ? 'text-emerald-600 font-extrabold' : isDraft ? 'text-amber-700 font-extrabold flex items-center gap-1' : 'text-white'}>
                    {isCustomer ? '📱 Client (Incoming)' : isAgent ? '👤 Agent Reply' : isDraft ? '🤖 Tori Draft (Needs Approve)' : '🤖 Tori (AI Auto-Reply)'}
                  </span>
                  <span className={`text-[9px] font-normal ${isCustomer ? 'text-slate-400' : isDraft ? 'text-amber-500' : 'text-indigo-100'}`}>
                    {m.at ? new Date(m.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
                  </span>
                </div>
                <p className="whitespace-pre-wrap font-medium leading-relaxed mt-0.5">{m.text}</p>
                {isDraft && onApproveDraft && onDiscardDraft && (
                  <div className="flex items-center gap-2 mt-2 pt-2 border-t border-amber-200/50 justify-end">
                    <button
                      onClick={() => onDiscardDraft(m.id)}
                      className="px-2.5 py-1 text-[9px] font-extrabold bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 rounded-md transition-colors cursor-pointer"
                    >
                      Discard
                    </button>
                    <button
                      onClick={() => onApproveDraft(m.id)}
                      className="px-2.5 py-1 text-[9px] font-extrabold bg-emerald-600 hover:bg-emerald-700 text-white rounded-md shadow-xs transition-colors cursor-pointer flex items-center gap-0.5"
                    >
                      Approve & Send
                    </button>
                  </div>
                )}
              </div>
            );
          })
        )}
        <div ref={messagesEndRef} />
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}

// Component wrapper for assistant-ui Thread
function CustomThreadWrapper({
  messages,
  events,
  onSendReply,
  isSendDisabled,
  onApproveDraft,
  onDiscardDraft
}: {
  messages: Message[];
  events?: any[];
  onSendReply: (text: string) => void;
  isSendDisabled: boolean;
  onApproveDraft?: (messageId: string) => void;
  onDiscardDraft?: (messageId: string) => void;
}) {
  const convertedMessages = useMemo(() => {
    return messages.map((m) => {
      let role: "user" | "assistant" | "system" = "system";
      if (m.role === 'customer') role = 'user';
      else if (m.role === 'agent') role = 'assistant';
      else if (m.role === 'system') role = 'system';

      return {
        id: m.id,
        role,
        content: [{ type: "text" as const, text: m.text }]
      };
    });
  }, [messages]);

  const runtime = useExternalStoreRuntime({
    messages: convertedMessages,
    convertMessage: (msg: any) => msg,
    setMessages: () => {},
    onNew: async (msg) => {
      const textPart = msg.content[0];
      if (textPart && textPart.type === "text") {
        onSendReply(textPart.text);
      }
    },
    isSendDisabled: isSendDisabled
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="flex-1 h-full overflow-hidden border border-slate-200 rounded-lg bg-white flex flex-col">
        <SmsAssistantThread
          messages={messages}
          events={events}
          onApproveDraft={onApproveDraft}
          onDiscardDraft={onDiscardDraft}
        />
      </div>
    </AssistantRuntimeProvider>
  );
}


function NotesPanelContent({
  notes,
  noteText,
  setNoteText,
  onSaveNote
}: {
  notes: any[];
  noteText: string;
  setNoteText: (val: string) => void;
  onSaveNote: (e: React.FormEvent) => void;
}) {
  return (
    <>
      {/* Notes List */}
      <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2.5">
        {notes.length === 0 ? (
          <div className="text-center py-6 text-xs text-slate-400">
            No internal notes yet. Keep track of customer triage details here.
          </div>
        ) : (
          notes.map((note) => (
            <div key={note.id} className="bg-white p-2.5 rounded border border-slate-200 shadow-sm flex flex-col gap-1">
              <div className="flex justify-between items-center text-[10px] text-slate-400">
                <span className="font-semibold text-slate-600 text-slate-500">Agent {note.agentId}</span>
                <span>{new Date(note.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
              </div>
              <p className="text-xs text-slate-700 whitespace-pre-wrap font-medium">{note.text}</p>
            </div>
          ))
        )}
      </div>

      {/* Add Note Form */}
      <form onSubmit={onSaveNote} className="p-3 border-t border-slate-200 bg-white flex flex-col gap-2 shrink-0">
        <textarea
          value={noteText}
          onChange={(e) => setNoteText(e.target.value)}
          placeholder="Add internal agent note..."
          className="w-full h-16 p-2 text-xs border border-slate-300 rounded focus:outline-none focus:ring-1 focus:ring-indigo-500 resize-none"
        />
        <button
          type="submit"
          disabled={!noteText.trim()}
          className="w-full bg-slate-800 hover:bg-slate-900 text-white text-xs py-1.5 rounded font-semibold flex items-center justify-center gap-1 transition-colors disabled:bg-slate-300 disabled:cursor-not-allowed cursor-pointer border border-transparent"
        >
          <Plus className="w-3.5 h-3.5" />
          Save Note
        </button>
      </form>
    </>
  );
}

export default function SmsTriageDashboard() {
  // Filters & Search
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('needs-review');
  const [priorityFilter, setPriorityFilter] = useState('all');
  const [showUnreadOnly, setShowUnreadOnly] = useState(false);

  // Thread Data
  const [threads, setThreads] = useState<ThreadListItem[]>([]);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [selectedThread, setSelectedThread] = useState<ThreadDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Form Inputs
  const [replyText, setReplyText] = useState('');
  const [sendingReply, setSendingReply] = useState(false);
  const [noteText, setNoteText] = useState('');
  const [requestedInformation, setRequestedInformation] = useState('');
  const [submittingInformation, setSubmittingInformation] = useState(false);
  const [showNotesMobile, setShowNotesMobile] = useState(false);
  const sendingReplyRef = useRef(false);
  const reviewingDraftRef = useRef<string | null>(null);

  // Tab switcher
  const [dashboardTab, setDashboardTab] = useState<'triage' | 'calendar'>('triage');

  // Calendar data
  const [bookings, setBookings] = useState<CalendarBooking[]>([]);
  const [freeBusySlots, setFreeBusySlots] = useState<FreeBusySlot[]>([]);
  const [deleteConfirmBookingId, setDeleteConfirmBookingId] = useState<string | null>(null);

  // Fetch threads list
  const fetchThreadsList = useCallback(async () => {
    try {
      const list = await listThreads({
        search: searchQuery || undefined,
        filterStatus: statusFilter !== 'all' ? statusFilter : undefined,
        filterPriority: priorityFilter !== 'all' ? priorityFilter : undefined,
        onlyUnread: showUnreadOnly ? true : undefined
      });
      setThreads(list);
    } catch (err) {
      console.error('Failed to load threads list:', err);
    }
  }, [searchQuery, statusFilter, priorityFilter, showUnreadOnly]);

  // Fetch single thread detail
  const fetchThreadDetail = useCallback(async (id: string, isSilent = false) => {
    if (!isSilent) setLoadingDetail(true);
    try {
      const detail = await getThread(id);
      setSelectedThread(detail);
    } catch (err) {
      console.error(`Failed to load thread ${id} details:`, err);
    } finally {
      if (!isSilent) setLoadingDetail(false);
    }
  }, []);

  // Fetch calendar bookings and slots
  const fetchCalendarData = useCallback(async () => {
    try {
      const [bList, fbList] = await Promise.all([
        listBookings().catch(err => {
          console.error("Failed to list bookings:", err);
          return [] as CalendarBooking[];
        }),
        getFreeBusy().catch(err => {
          console.error("Failed to get freebusy:", err);
          return [] as FreeBusySlot[];
        })
      ]);
      setBookings(bList);
      setFreeBusySlots(fbList);
    } catch (err) {
      console.error('Failed to load calendar data:', err);
    }
  }, []);

  // Poll thread list
  useEffect(() => {
    fetchThreadsList();
    const interval = setInterval(fetchThreadsList, 4000);
    return () => clearInterval(interval);
  }, [fetchThreadsList]);

  // Poll selected thread details
  useEffect(() => {
    if (!selectedThreadId) {
      setSelectedThread(null);
      return;
    }
    fetchThreadDetail(selectedThreadId, false);
    const interval = setInterval(() => {
      fetchThreadDetail(selectedThreadId, true);
    }, 4000);
    return () => clearInterval(interval);
  }, [selectedThreadId, fetchThreadDetail]);

  // Poll calendar data
  useEffect(() => {
    let interval: any;
    if (dashboardTab === 'calendar') {
      fetchCalendarData();
      interval = setInterval(fetchCalendarData, 4000);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [dashboardTab, fetchCalendarData]);

  // Handlers
  const handleSelectThread = (id: string) => {
    setSelectedThreadId(id);
  };

  const handleTakeOver = async () => {
    if (!selectedThreadId) return;
    try {
      await takeOverThread(selectedThreadId, CURRENT_AGENT_ID);
      await Promise.all([fetchThreadsList(), fetchThreadDetail(selectedThreadId, true)]);
    } catch (err) {
      alert('Failed to take over thread');
    }
  };

  const handleSendReply = async (text: string) => {
    if (!selectedThreadId || !text.trim() || sendingReplyRef.current) return;
    sendingReplyRef.current = true;
    setSendingReply(true);
    try {
      if (selectedThread && selectedThread.state !== 'taken-over' && selectedThread.state !== 'escalated') {
        await takeOverThread(selectedThreadId, CURRENT_AGENT_ID);
      }
      await sendThreadReply(selectedThreadId, CURRENT_AGENT_ID, text, crypto.randomUUID());
      setReplyText('');
      await Promise.all([fetchThreadsList(), fetchThreadDetail(selectedThreadId, true)]);
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to send reply');
    } finally {
      sendingReplyRef.current = false;
      setSendingReply(false);
    }
  };

  const pendingInformationRequest = useMemo(() => {
    if (selectedThread?.state !== 'needs-review') return null;
    return [...(selectedThread?.events ?? [])].reverse().find(event =>
      (event.type === 'information-request' || event.type === 'catch-up-handoff')
      && event.meta?.status !== 'resolved'
    ) ?? null;
  }, [selectedThread?.state, selectedThread?.events]);

  const handleInformationRequest = async () => {
    if (!selectedThreadId || !pendingInformationRequest || !requestedInformation.trim() || submittingInformation) return;
    setSubmittingInformation(true);
    try {
      await respondToInformationRequest(
        selectedThreadId,
        pendingInformationRequest.id,
        CURRENT_AGENT_ID,
        requestedInformation.trim(),
      );
      setRequestedInformation('');
      await Promise.all([fetchThreadsList(), fetchThreadDetail(selectedThreadId, true)]);
    } catch (err) {
      alert(err instanceof Error ? err.message : 'The information could not be used. No reply was sent.');
    } finally {
      setSubmittingInformation(false);
    }
  };

  const handleApproveDraft = async (messageId: string) => {
    if (!selectedThreadId || reviewingDraftRef.current) return;
    reviewingDraftRef.current = messageId;
    try {
      await approveDraft(messageId);
      await Promise.all([fetchThreadsList(), fetchThreadDetail(selectedThreadId, true)]);
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to approve draft message');
    } finally {
      reviewingDraftRef.current = null;
    }
  };

  const handleDiscardDraft = async (messageId: string) => {
    if (!selectedThreadId) return;
    try {
      await discardDraft(messageId);
      await Promise.all([fetchThreadsList(), fetchThreadDetail(selectedThreadId, true)]);
    } catch (err) {
      alert('Failed to discard draft message');
    }
  };

  const handleAddNote = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedThreadId || !noteText.trim()) return;
    try {
      await addThreadNote(selectedThreadId, CURRENT_AGENT_ID, noteText);
      setNoteText('');
      await fetchThreadDetail(selectedThreadId, true);
    } catch (err) {
      alert('Failed to add note');
    }
  };

  const handleEscalate = async () => {
    if (!selectedThreadId) return;
    const reason = prompt('Please enter a reason for escalation:');
    if (reason === null) return; // cancelled
    try {
      await escalateThread(selectedThreadId, CURRENT_AGENT_ID, reason || 'Escalated by agent');
      await Promise.all([fetchThreadsList(), fetchThreadDetail(selectedThreadId, true)]);
    } catch (err) {
      alert('Failed to escalate thread');
    }
  };

  const handleResolve = async () => {
    if (!selectedThreadId) return;
    const resolution = prompt('Please enter a resolution note:');
    if (resolution === null) return; // cancelled
    try {
      await resolveThread(selectedThreadId, CURRENT_AGENT_ID, resolution || 'Resolved by agent');
      await Promise.all([fetchThreadsList(), fetchThreadDetail(selectedThreadId, true)]);
    } catch (err) {
      alert('Failed to resolve thread');
    }
  };

  const confirmDeleteBooking = async (id: string) => {
    try {
      await deleteBooking(id);
      await fetchCalendarData();
    } catch (err) {
      alert('Failed to delete booking');
    }
  };


  // Helper styles for badges
  const getStatusBadgeClass = (status: string) => {
    switch (status) {
      case 'auto-reply':
        return 'bg-blue-100 text-blue-800 border border-blue-200';
      case 'needs-review':
        return 'bg-yellow-100 text-yellow-800 border border-yellow-200';
      case 'taken-over':
        return 'bg-purple-100 text-purple-800 border border-purple-200';
      case 'escalated':
        return 'bg-red-100 text-red-800 border border-red-200';
      case 'resolved':
        return 'bg-green-100 text-green-800 border border-green-200';
      default:
        return 'bg-slate-100 text-slate-800 border border-slate-200';
    }
  };

  const getPriorityBadgeClass = (priority: string) => {
    switch (priority) {
      case 'high':
        return 'bg-rose-100 text-rose-700 font-semibold';
      case 'medium':
        return 'bg-amber-100 text-amber-700';
      case 'low':
        return 'bg-slate-100 text-slate-600';
      default:
        return 'bg-slate-100 text-slate-600';
    }
  };

  const getSlaBadgeClass = (slaStatus: string) => {
    switch (slaStatus) {
      case 'breached':
        return 'bg-rose-600 text-white font-bold animate-pulse';
      case 'breaching':
        return 'bg-amber-500 text-white font-semibold';
      case 'ok':
        return 'bg-emerald-600 text-white';
      default:
        return 'bg-slate-500 text-white';
    }
  };

  const isReplyEnabled = selectedThread !== null;

  return (
    <div className="flex h-full w-full overflow-hidden bg-slate-100 font-sans">
      {/* Sidebar */}
      <div className={`w-full md:w-80 flex flex-col bg-white border-r border-slate-200 shrink-0 ${selectedThreadId ? 'hidden md:flex' : 'flex'}`}>
        {/* Title */}
        <div className="p-4 border-b border-slate-200 flex items-center gap-2 shrink-0">
          <Phone className="w-5 h-5 text-indigo-600" />
          <h1 className="text-lg font-bold text-slate-800">SMS Triage Dashboard</h1>
        </div>

        {/* Filters */}
        <div className="p-4 border-b border-slate-200 flex flex-col gap-3 shrink-0">
          {/* Search */}
          <div className="relative">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-2.5" />
            <input
              type="text"
              placeholder="Search phone or body..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-3 py-1.5 text-sm bg-slate-50 border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:bg-white text-slate-800"
            />
          </div>

          <div className="grid grid-cols-2 gap-2">
            {/* Status Filter */}
            <div>
              <label className="block text-[10px] font-bold text-slate-500 mb-1 flex items-center gap-1 uppercase tracking-wider">
                <Filter className="w-3 h-3" /> Status
              </label>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="w-full text-xs bg-slate-50 border border-slate-300 rounded p-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 text-slate-700 font-medium"
              >
                <option value="all">All States</option>
                <option value="auto-reply">Auto Reply</option>
                <option value="needs-review">Needs Review</option>
                <option value="taken-over">Taken Over</option>
                <option value="escalated">Escalated</option>
                <option value="resolved">Resolved</option>
              </select>
            </div>

            {/* Priority Filter */}
            <div>
              <label className="block text-[10px] font-bold text-slate-500 mb-1 flex items-center gap-1 uppercase tracking-wider">
                <Filter className="w-3 h-3" /> Priority
              </label>
              <select
                value={priorityFilter}
                onChange={(e) => setPriorityFilter(e.target.value)}
                className="w-full text-xs bg-slate-50 border border-slate-300 rounded p-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 text-slate-700 font-medium"
              >
                <option value="all">All Priorities</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </div>
          </div>

          {/* Unread Toggle */}
          <label className="flex items-center gap-2 text-xs font-semibold text-slate-700 cursor-pointer pt-1">
            <input
              type="checkbox"
              checked={showUnreadOnly}
              onChange={(e) => setShowUnreadOnly(e.target.checked)}
              className="rounded text-indigo-600 focus:ring-indigo-500 w-4 h-4 cursor-pointer border-slate-300"
            />
            <span>Show Unread Only</span>
          </label>
        </div>

        {/* Thread List */}
        <div className="flex-1 overflow-y-auto divide-y divide-slate-100">
          {threads.length === 0 ? (
            <div className="p-8 text-center text-sm text-slate-400 font-medium">
              No threads found
            </div>
          ) : (
            threads.map((t) => (
              <div
                key={t.id}
                onClick={() => handleSelectThread(t.id)}
                className={`p-3.5 cursor-pointer transition-colors flex flex-col gap-2 hover:bg-slate-50 ${
                  selectedThreadId === t.id ? 'bg-indigo-50/60 border-l-4 border-indigo-600 pl-2.5' : ''
                }`}
              >
                <div className="flex justify-between items-center">
                  <span className="font-bold text-slate-800 text-sm tracking-tight">{t.customerPhone}</span>
                  {t.unreadCount > 0 && (
                    <span className="bg-rose-500 text-white text-[9px] font-extrabold px-2 py-0.5 rounded-full shadow-xs">
                      {t.unreadCount} unread
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className={`text-[9px] font-bold px-2 py-0.5 rounded-full shadow-2xs ${getStatusBadgeClass(t.status)}`}>
                    {t.status}
                  </span>
                  <span className={`text-[9px] font-bold px-2 py-0.5 rounded-full uppercase shadow-2xs ${getPriorityBadgeClass(t.priority)}`}>
                    {t.priority}
                  </span>
                </div>

                <div className="flex items-center justify-between text-[10px] text-slate-400 font-semibold mt-0.5">
                  <span className="flex items-center gap-1">
                    <Clock className="w-3.5 h-3.5 text-slate-300" />
                    {t.lastMessageAt ? new Date(t.lastMessageAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
                  </span>
                  {t.assignedAgentName && (
                    <span className="truncate max-w-[120px] font-bold text-indigo-500">
                      👤 {t.assignedAgentName}
                    </span>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Main Panel */}
      <div className={`flex-1 flex flex-col overflow-hidden bg-slate-50 ${selectedThreadId ? 'flex' : 'hidden md:flex'}`}>
        {/* Workspace Tab Bar */}
        <div className="bg-slate-900 text-white px-4 py-2.5 flex flex-col sm:flex-row justify-between items-center gap-2 shadow-sm shrink-0 border-b border-slate-700">
          <span className="text-xs font-bold tracking-wider uppercase text-slate-400">Triage Workspace</span>
          <div className="flex bg-slate-800 p-0.5 rounded border border-slate-700 shrink-0">
            <button
              onClick={() => setDashboardTab('triage')}
              className={`px-3 py-1 rounded text-[11px] font-bold transition-all cursor-pointer ${
                dashboardTab === 'triage' ? 'bg-indigo-600 text-white shadow-xs' : 'text-slate-400 hover:text-white'
              }`}
            >
              Conversation Inbox
            </button>
            <button
              onClick={() => setDashboardTab('calendar')}
              className={`px-3 py-1 rounded text-[11px] font-bold transition-all cursor-pointer flex items-center gap-1 ${
                dashboardTab === 'calendar' ? 'bg-indigo-600 text-white shadow-xs' : 'text-slate-400 hover:text-white'
              }`}
            >
              <Calendar className="w-3.5 h-3.5" />
              Calendar Manager
            </button>
          </div>
        </div>

        {dashboardTab === 'calendar' ? (
          <div className="flex-1 flex flex-col md:flex-row overflow-y-auto md:overflow-hidden p-4 md:p-6 gap-4 md:gap-6 bg-slate-50">
            {/* Upcoming Bookings column */}
            <div className="flex-1 flex flex-col bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden min-h-[350px] md:min-h-0">
              <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center gap-2 shrink-0">
                <CalendarCheck className="w-5 h-5 text-indigo-600" />
                <h3 className="font-bold text-slate-800 text-sm">Upcoming Appointments</h3>
              </div>
              <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
                {bookings.length === 0 ? (
                  <div className="flex-1 flex flex-col items-center justify-center text-slate-400 py-12">
                    <Calendar className="w-12 h-12 text-slate-300 stroke-[1.5] mb-2" />
                    <p className="text-xs font-semibold">No bookings scheduled yet</p>
                    <p className="text-[10px] text-slate-400">Appointments scheduled by AI or agents will appear here.</p>
                  </div>
                ) : (
                  bookings.map((booking) => (
                    <div
                      key={booking.id}
                      className="bg-white border-l-4 border-indigo-600 border border-slate-200 rounded-lg p-3.5 shadow-xs hover:shadow-sm transition-shadow flex flex-col gap-1.5"
                    >
                      <div className="flex justify-between items-start">
                        <span className="font-bold text-slate-800 text-sm">{booking.summary}</span>
                      </div>
                      <div className="flex flex-col gap-1 text-xs text-slate-500">
                        <span className="flex items-center gap-1.5 font-semibold text-slate-700">
                          <Phone className="w-3.5 h-3.5 text-slate-400" /> {booking.customerPhone || 'N/A'}
                        </span>
                        <span className="flex items-center gap-1.5 font-medium">
                          <Clock className="w-3.5 h-3.5 text-slate-400" />
                          {new Date(booking.startTime).toLocaleString('en-AU', { timeZone: 'Australia/Hobart', dateStyle: 'short', timeStyle: 'short' })} - {new Date(booking.endTime).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZone: 'Australia/Hobart' })}
                        </span>
                      </div>
                      <div className="flex justify-between items-center mt-2 pt-2 border-t border-slate-100 shrink-0">
                        <span className="text-[10px] text-slate-400">ID: {booking.id.slice(0, 8)}</span>
                        <button
                          onClick={() => setDeleteConfirmBookingId(booking.id)}
                          className="text-[10.5px] font-bold text-rose-600 hover:text-rose-800 hover:underline cursor-pointer bg-transparent border-none p-0"
                        >
                          Cancel Appointment
                        </button>
                      </div>

                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Free/Busy Slots column */}
            <div className="w-full md:w-80 flex flex-col bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden min-h-[350px] md:min-h-0">
              <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center gap-2 shrink-0">
                <Clock className="w-5 h-5 text-indigo-600" />
                <h3 className="font-bold text-slate-800 text-sm">Free/Busy Availability</h3>
              </div>
              <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-2.5">
                {freeBusySlots.length === 0 ? (
                  <div className="text-center py-12 text-xs text-slate-400 font-medium">
                    No active slots defined on the calendar.
                  </div>
                ) : (
                  freeBusySlots.map((slot, index) => (
                    <div
                      key={index}
                      className="bg-emerald-50/50 border border-emerald-200 rounded-lg p-2.5 flex flex-col gap-1.5"
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full bg-emerald-500"></span>
                        <span className="text-xs font-bold text-emerald-800">Available Slot</span>
                      </div>
                      <div className="text-[11px] text-slate-600 font-medium flex flex-col">
                        <span>Start: {new Date(slot.startTime).toLocaleString('en-AU', { timeZone: 'Australia/Hobart', dateStyle: 'short', timeStyle: 'short' })}</span>
                        <span>End: {new Date(slot.endTime).toLocaleString('en-AU', { timeZone: 'Australia/Hobart', dateStyle: 'short', timeStyle: 'short' })}</span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        ) : !selectedThread ? (
          <div className="flex-1 flex flex-col justify-center items-center text-slate-400 p-8 text-center">
            <Phone className="w-16 h-16 mb-4 text-slate-300 stroke-[1.5]" />
            <h2 className="text-xl font-semibold text-slate-600 mb-1">No conversation selected</h2>
            <p className="text-sm max-w-xs">Select a thread from the sidebar list to begin triage, internal notes, and replies.</p>
          </div>
        ) : (
          <>
            {/* Header */}
            <div className="bg-white border-b border-slate-200 p-3 sm:p-4 flex flex-wrap justify-between items-center gap-3 shadow-2xs shrink-0">
              <div className="flex items-center gap-2">
                {/* Mobile Back Button */}
                <button
                  onClick={() => setSelectedThreadId(null)}
                  className="p-1.5 rounded-full hover:bg-slate-100 text-slate-500 hover:text-slate-700 md:hidden cursor-pointer shrink-0 transition-colors"
                >
                  <ArrowLeft className="w-5.5 h-5.5" />
                </button>
                <div className="flex flex-col gap-0.5 sm:gap-1">
                  <div className="flex items-center gap-1.5 sm:gap-2">
                    <h2 className="text-sm sm:text-lg font-bold text-slate-800 tracking-tight">{selectedThread.customerPhone}</h2>
                    <span className={`text-[9px] sm:text-xs px-2 py-0.5 rounded-full font-bold shadow-2xs ${getStatusBadgeClass(selectedThread.state)}`}>
                      {selectedThread.state}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 sm:gap-3 text-[10px] sm:text-xs text-slate-500 flex-wrap font-semibold">
                    <span className="flex items-center gap-1 text-slate-500">
                      <User className="w-3.5 h-3.5 text-slate-400" />
                      Assigned: {selectedThread.assignedAgent ? selectedThread.assignedAgent.name : 'Unassigned'}
                    </span>
                    <span className="hidden sm:inline opacity-40">•</span>
                    <span className="flex items-center gap-1 text-slate-400">
                      <Clock className="w-3.5 h-3.5 text-slate-300" />
                      SLA: {new Date(selectedThread.sla.dueAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-1.5 sm:gap-2 w-full sm:w-auto justify-end sm:justify-start">
                {/* Mobile Notes Button */}
                <button
                  onClick={() => setShowNotesMobile(true)}
                  className="flex items-center gap-1 bg-slate-100 hover:bg-slate-200 text-slate-700 text-[10px] sm:text-xs px-2.5 py-1.5 rounded font-bold border border-slate-200 lg:hidden cursor-pointer transition-colors mr-auto sm:mr-0"
                >
                  <FileText className="w-3.5 h-3.5 text-slate-500" />
                  <span>Notes ({selectedThread.notes.length})</span>
                </button>

                {/* SLA Badge */}
                <div className={`text-[10px] sm:text-xs px-2 py-1.5 rounded font-bold flex items-center gap-1 ${getSlaBadgeClass(selectedThread.sla.status)}`}>
                  {selectedThread.sla.status === 'breached' && <AlertCircle className="w-3.5 h-3.5 text-white animate-pulse" />}
                  {selectedThread.sla.status === 'breaching' && <AlertTriangle className="w-3.5 h-3.5 text-white" />}
                  {selectedThread.sla.status === 'ok' && <CheckCircle2 className="w-3.5 h-3.5 text-white" />}
                  SLA: {selectedThread.sla.status.toUpperCase()}
                </div>

                {/* Quick actions container */}
                <div className="flex gap-1">
                  {selectedThread.state !== 'taken-over' && (
                    <button
                      onClick={handleTakeOver}
                      className="bg-indigo-600 hover:bg-indigo-700 text-white text-[10px] sm:text-xs px-2.5 py-1.5 rounded font-semibold shadow-2xs transition-colors cursor-pointer"
                    >
                      Take Over
                    </button>
                  )}

                  {selectedThread.state !== 'resolved' && (
                    <>
                      <button
                        onClick={handleEscalate}
                        className="bg-amber-500 hover:bg-amber-600 text-white text-[10px] sm:text-xs px-2.5 py-1.5 rounded font-semibold shadow-2xs transition-colors cursor-pointer"
                      >
                        Escalate
                      </button>
                      <button
                        onClick={handleResolve}
                        className="bg-emerald-600 hover:bg-emerald-700 text-white text-[10px] sm:text-xs px-2.5 py-1.5 rounded font-semibold shadow-2xs transition-colors cursor-pointer"
                      >
                        Resolve
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>

            {/* Split Content Area */}
            <div className="flex-1 flex overflow-hidden relative">
              {/* Conversation Area */}
              <div className="flex-1 flex flex-col p-3 sm:p-4 overflow-hidden gap-3 sm:gap-4">
                {/* Assistant UI Thread */}
                {loadingDetail ? (
                  <div className="flex-1 flex items-center justify-center text-slate-400 font-semibold">
                    Loading messages...
                  </div>
                ) : (
                  <CustomThreadWrapper
                    messages={selectedThread.messages}
                    events={selectedThread.events}
                    onSendReply={handleSendReply}
                    isSendDisabled={!isReplyEnabled || sendingReply}
                    onApproveDraft={handleApproveDraft}
                    onDiscardDraft={handleDiscardDraft}
                  />
                )}

                {pendingInformationRequest && (
                  <div className="shrink-0 rounded-xl border border-amber-300 bg-amber-50 p-3 shadow-sm">
                    <p className="text-[10px] font-extrabold uppercase tracking-wider text-amber-700">Information Request</p>
                    <p className="mt-1 text-xs font-semibold text-slate-800">
                      Tori needs: {String(pendingInformationRequest.meta?.reason || 'More business information to answer this message safely.')}
                    </p>
                    <p className="mt-1 text-[10px] text-slate-500">Nothing has been sent. Supply the missing facts and Tori will format them, save reusable knowledge, and reply.</p>
                    <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                      <textarea
                        value={requestedInformation}
                        onChange={(event) => setRequestedInformation(event.target.value)}
                        rows={2}
                        maxLength={6000}
                        placeholder="Type the information Tori needs..."
                        className="min-h-[52px] flex-1 resize-none rounded-lg border border-slate-300 bg-white p-2 text-xs outline-none focus:ring-2 focus:ring-amber-500"
                      />
                      <button
                        type="button"
                        onClick={handleInformationRequest}
                        disabled={!requestedInformation.trim() || submittingInformation}
                        className="rounded-lg bg-amber-600 px-4 py-2 text-xs font-bold text-white disabled:bg-slate-300"
                      >
                        {submittingInformation ? 'Preparing...' : 'Save Knowledge & Send'}
                      </button>
                    </div>
                  </div>
                )}

                {/* Custom Human Reply Composer */}
                <div className="bg-white p-3 border border-slate-200 rounded-lg shadow-2xs flex flex-col gap-2 shrink-0">
                  <div className="flex justify-between items-center text-[10px] sm:text-xs text-slate-500 border-b border-slate-100 pb-1.5 font-semibold">
                    <span className="font-bold text-slate-700">Agent SMS Composer</span>
                    <span>
                      {isReplyEnabled
                        ? `Sending as Agent ${CURRENT_AGENT_ID}`
                        : 'Takeover thread or escalate to compose'}
                    </span>
                  </div>
                  <div className="flex gap-2">
                    <textarea
                      value={replyText}
                      onChange={(e) => setReplyText(e.target.value)}
                      disabled={!isReplyEnabled || sendingReply}
                      placeholder={
                        isReplyEnabled
                          ? 'Type your SMS reply here...'
                          : 'SMS composition is disabled. Please Take Over this conversation.'
                      }
                      className="flex-1 min-h-[40px] max-h-[120px] p-2 text-xs sm:text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:bg-slate-50 disabled:text-slate-400 resize-none font-medium text-slate-800"
                    />
                    <button
                      onClick={() => handleSendReply(replyText)}
                      disabled={!isReplyEnabled || !replyText.trim() || sendingReply}
                      className="bg-indigo-600 hover:bg-indigo-700 text-white text-xs sm:text-sm px-3.5 sm:px-4 rounded-md font-semibold flex items-center gap-1.5 transition-colors disabled:bg-slate-300 disabled:cursor-not-allowed cursor-pointer"
                    >
                      <Send className="w-3.5 sm:w-4 sm:h-4 h-3.5" />
                      <span>{sendingReply ? 'Sending...' : 'Send'}</span>
                    </button>
                  </div>
                </div>
              </div>

              {/* Notes Panel (Inline desktop version) */}
              <div className="w-80 border-l border-slate-200 bg-slate-50 hidden lg:flex flex-col overflow-hidden">
                <div className="p-3 border-b border-slate-200 bg-white flex items-center gap-1.5 shrink-0">
                  <FileText className="w-4 h-4 text-slate-600" />
                  <span className="font-bold text-sm text-slate-700">Internal Notes</span>
                </div>
                <NotesPanelContent
                  notes={selectedThread.notes}
                  noteText={noteText}
                  setNoteText={setNoteText}
                  onSaveNote={handleAddNote}
                />
              </div>

              {/* Mobile Notes Drawer Backdrop */}
              {showNotesMobile && (
                <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-xs z-50 flex justify-end lg:hidden">
                  {/* Drawer Content */}
                  <div className="w-80 max-w-[85%] bg-white h-full shadow-2xl flex flex-col animate-slide-left">
                    {/* Drawer Header */}
                    <div className="p-4 border-b border-slate-200 flex justify-between items-center bg-slate-50 shrink-0">
                      <div className="flex items-center gap-2">
                        <FileText className="w-4 h-4 text-slate-700" />
                        <span className="font-bold text-sm text-slate-800">Internal Notes</span>
                      </div>
                      <button
                        onClick={() => setShowNotesMobile(false)}
                        className="p-1 rounded hover:bg-slate-200 text-slate-500 hover:text-slate-700 cursor-pointer transition-colors"
                      >
                        <X className="w-5 h-5" />
                      </button>
                    </div>

                    <NotesPanelContent
                      notes={selectedThread.notes}
                      noteText={noteText}
                      setNoteText={setNoteText}
                      onSaveNote={handleAddNote}
                    />
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {deleteConfirmBookingId !== null && (
        <div className="fixed inset-0 bg-slate-950/60 backdrop-blur-xs flex items-center justify-center p-4 z-50 animate-fadeIn" style={{ animation: 'stepFadeIn 0.2s ease both' }}>
          <div className="bg-white rounded-2xl border border-slate-150 p-6 shadow-2xl max-w-sm w-full flex flex-col gap-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-rose-50 flex items-center justify-center text-rose-600">
                <Trash2 className="w-5.5 h-5.5" />
              </div>
              <h3 className="text-base font-bold text-slate-800">Cancel Appointment?</h3>
            </div>
            <p className="text-xs text-slate-500 font-semibold leading-relaxed">
              Are you sure you want to cancel and delete this booking? This action is permanent and will reopen this slot on the calendar.
            </p>
            <div className="flex justify-end gap-2.5 mt-2">
              <button
                type="button"
                onClick={() => setDeleteConfirmBookingId(null)}
                className="px-4 py-2 rounded-xl border border-slate-200 text-xs font-bold text-slate-500 hover:bg-slate-50 active:bg-slate-100 transition-colors cursor-pointer bg-white"
              >
                No, Keep
              </button>
              <button
                type="button"
                onClick={() => {
                  const id = deleteConfirmBookingId;
                  setDeleteConfirmBookingId(null);
                  confirmDeleteBooking(id);
                }}
                className="px-4 py-2 rounded-xl bg-[#7a0b2e] hover:bg-[#5c0822] text-white text-xs font-bold transition-colors cursor-pointer"
              >
                Yes, Cancel Booking
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
