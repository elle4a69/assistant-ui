import { FormEvent, KeyboardEvent, ReactNode, useEffect, useRef, useState } from 'react';
import { Bot, CornerDownLeft, Mic, PhoneOff, RefreshCw, Send, ShieldCheck, UserRound, Volume2 } from 'lucide-react';
import {
  createOperationsRealtimeSession,
  getOperationsChatMessages,
  OperationsChatMessage,
  sendOperationsChatMessage,
} from './api';

const OPERATIONS_URL_PATTERN = /(https?:\/\/[^\s<>\])]+)/g;

function renderLinkedText(content: string): ReactNode[] {
  return content.split(OPERATIONS_URL_PATTERN).map((part, index) => (
    part.startsWith('https://') || part.startsWith('http://')
      ? <a key={`${part}-${index}`} href={part} target="_blank" rel="noreferrer" className="font-semibold text-indigo-700 underline decoration-indigo-300 underline-offset-2">{part}</a>
      : part
  ));
}


export default function OperationsAIChat() {
  const [messages, setMessages] = useState<OperationsChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [voiceState, setVoiceState] = useState<'idle' | 'connecting' | 'live'>('idle');
  const [error, setError] = useState<string | null>(null);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);
  const peerConnectionRef = useRef<RTCPeerConnection | null>(null);
  const microphoneStreamRef = useRef<MediaStream | null>(null);
  const audioElementRef = useRef<HTMLAudioElement | null>(null);

  const loadMessages = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getOperationsChatMessages();
      setMessages(result.messages);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Could not load the chat.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadMessages();
    return () => stopVoice();
  }, []);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [messages, sending]);

  const submitMessage = async (event?: FormEvent) => {
    event?.preventDefault();
    const message = draft.trim();
    if (!message || sending) return;

    const optimisticMessage: OperationsChatMessage = {
      id: `pending-${Date.now()}`,
      role: 'user',
      content: message,
      createdAt: new Date().toISOString(),
    };
    setDraft('');
    setError(null);
    setSending(true);
    setMessages((current) => [...current, optimisticMessage]);
    try {
      const result = await sendOperationsChatMessage(message);
      setMessages((current) => [
        ...current.filter((item) => item.id !== optimisticMessage.id),
        result.userMessage,
        result.assistantMessage,
      ]);
    } catch (requestError) {
      setMessages((current) => current.filter((item) => item.id !== optimisticMessage.id));
      setDraft(message);
      setError(requestError instanceof Error ? requestError.message : 'The operations AI could not answer.');
    } finally {
      setSending(false);
    }
  };

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void submitMessage();
    }
  };

  const stopVoice = () => {
    microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
    microphoneStreamRef.current = null;
    peerConnectionRef.current?.close();
    peerConnectionRef.current = null;
    if (audioElementRef.current) {
      audioElementRef.current.pause();
      audioElementRef.current.srcObject = null;
      audioElementRef.current.remove();
      audioElementRef.current = null;
    }
    setVoiceState('idle');
  };

  const startVoice = async () => {
    if (voiceState !== 'idle') return;
    setError(null);
    setVoiceState('connecting');
    try {
      const microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      microphoneStreamRef.current = microphoneStream;

      const peerConnection = new RTCPeerConnection();
      peerConnectionRef.current = peerConnection;
      const audioElement = document.createElement('audio');
      audioElement.autoplay = true;
      audioElementRef.current = audioElement;
      peerConnection.ontrack = (event) => {
        audioElement.srcObject = event.streams[0];
        void audioElement.play().catch(() => undefined);
      };
      peerConnection.onconnectionstatechange = () => {
        if (peerConnection.connectionState === 'connected') setVoiceState('live');
        if (['failed', 'disconnected', 'closed'].includes(peerConnection.connectionState)) stopVoice();
      };
      microphoneStream.getAudioTracks().forEach((track) => peerConnection.addTrack(track, microphoneStream));
      peerConnection.createDataChannel('oai-events');

      const offer = await peerConnection.createOffer();
      await peerConnection.setLocalDescription(offer);
      if (!offer.sdp) throw new Error('The browser could not create a realtime audio offer.');
      const answerSdp = await createOperationsRealtimeSession(offer.sdp);
      await peerConnection.setRemoteDescription({ type: 'answer', sdp: answerSdp });
    } catch (requestError) {
      stopVoice();
      if (requestError instanceof DOMException && requestError.name === 'NotAllowedError') {
        setError('Microphone access was declined. Allow microphone access in this browser to use realtime voice.');
      } else {
        setError(requestError instanceof Error ? requestError.message : 'Realtime voice could not start.');
      }
    }
  };

  return (
    <section className="overflow-hidden rounded-xl border border-indigo-200 bg-white shadow-sm" aria-labelledby="operations-ai-title">
      <div className="flex flex-col gap-3 border-b border-indigo-100 bg-gradient-to-r from-slate-950 via-indigo-950 to-slate-950 px-4 py-4 text-white sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10 ring-1 ring-white/20">
            <Bot className="h-5 w-5 text-indigo-200" />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 id="operations-ai-title" className="text-sm font-bold">Operations AI</h2>
              <span className="rounded-full border border-emerald-300/30 bg-emerald-300/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider text-emerald-200">
                Practical operator
              </span>
            </div>
            <p className="mt-1 text-[10px] leading-relaxed text-slate-300">
              Describe the outcome. It investigates, acts within its permissions, verifies, and reports back plainly.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {voiceState === 'idle' ? (
            <button
              type="button"
              onClick={() => void startVoice()}
              data-testid="operations-voice-start"
              className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-indigo-300/30 bg-indigo-300/10 px-3 py-2 text-[10px] font-bold text-indigo-100 transition hover:bg-indigo-300/20"
            >
              <Mic className="h-3.5 w-3.5" /> Start realtime voice
            </button>
          ) : (
            <button
              type="button"
              onClick={stopVoice}
              data-testid="operations-voice-stop"
              className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-rose-300/30 bg-rose-300/10 px-3 py-2 text-[10px] font-bold text-rose-100 transition hover:bg-rose-300/20"
            >
              {voiceState === 'connecting' ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <PhoneOff className="h-3.5 w-3.5" />}
              {voiceState === 'connecting' ? 'Connecting voice…' : 'End voice conversation'}
            </button>
          )}
          <button
            type="button"
            onClick={() => void loadMessages()}
            disabled={loading || sending}
            data-testid="operations-chat-refresh"
            className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-[10px] font-bold text-slate-200 transition hover:bg-white/10 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh history
          </button>
        </div>
      </div>

      {voiceState !== 'idle' && (
        <div className="flex items-center justify-between gap-3 border-b border-emerald-200 bg-emerald-50 px-4 py-2.5 text-[10px] font-semibold text-emerald-900" role="status">
          <span className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full bg-emerald-500 ${voiceState === 'live' ? 'animate-pulse' : ''}`} />
            {voiceState === 'live' ? 'Realtime voice is live—speak naturally and interrupt when needed.' : 'Connecting microphone and voice…'}
          </span>
          <Volume2 className="h-4 w-4 shrink-0 text-emerald-700" />
        </div>
      )}

      <div className="flex items-start gap-2 border-b border-amber-200 bg-amber-50 px-4 py-3 text-[10px] leading-relaxed text-amber-900">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
        <p>
          Text chat can self-diagnose message handling, search the internet with sources, and keep non-secret operational memory. Customer data is excluded from web queries and memory. Every operational setting change still requires a separate exact confirmation and is audit logged. It cannot access secrets, run arbitrary shell commands or SQL, edit code, deploy, send SMS, modify bookings, delete data, or perform bulk actions. Voice remains advisory and is not added to persistent text history.
        </p>
      </div>

      <div
        className="h-[420px] overflow-y-auto bg-slate-50 px-4 py-5"
        role="log"
        aria-live="polite"
        aria-label="Operations AI conversation"
        data-testid="operations-chat-transcript"
      >
        {loading && messages.length === 0 ? (
          <div className="flex h-full items-center justify-center gap-2 text-xs font-semibold text-indigo-700">
            <RefreshCw className="h-4 w-4 animate-spin" /> Loading conversation…
          </div>
        ) : messages.length === 0 ? (
          <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center text-center">
            <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-indigo-100 text-indigo-700">
              <Bot className="h-6 w-6" />
            </div>
            <h3 className="text-sm font-bold text-slate-900">Start a private system conversation</h3>
            <p className="mt-2 text-xs leading-relaxed text-slate-500">
              Ask it to diagnose recent message handling, research a technical issue, recall a decision, or record a durable lesson.
            </p>
          </div>
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-4">
            {messages.map((message) => (
              <div key={message.id} className={`flex gap-2.5 ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                {message.role === 'assistant' && (
                  <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-indigo-100 text-indigo-700">
                    <Bot className="h-3.5 w-3.5" />
                  </div>
                )}
                <div className={`max-w-[82%] whitespace-pre-wrap rounded-2xl px-3.5 py-2.5 text-xs leading-relaxed shadow-sm ${
                  message.role === 'user'
                    ? 'rounded-br-md bg-indigo-600 text-white'
                    : 'rounded-bl-md border border-slate-200 bg-white text-slate-800'
                }`}>
                  {message.role === 'assistant' ? renderLinkedText(message.content) : message.content}
                </div>
                {message.role === 'user' && (
                  <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-slate-200 text-slate-700">
                    <UserRound className="h-3.5 w-3.5" />
                  </div>
                )}
              </div>
            ))}
            {sending && (
              <div className="flex items-center gap-2.5">
                <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-100 text-indigo-700">
                  <Bot className="h-3.5 w-3.5" />
                </div>
                <div className="rounded-2xl rounded-bl-md border border-indigo-100 bg-white px-3.5 py-2.5 text-xs font-semibold text-indigo-600 shadow-sm">
                  Considering the available evidence…
                </div>
              </div>
            )}
            <div ref={transcriptEndRef} />
          </div>
        )}
      </div>

      <form onSubmit={submitMessage} className="border-t border-slate-200 bg-white p-4">
        {error && (
          <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[10px] font-semibold text-rose-800" role="alert">
            {error}
          </div>
        )}
        <div className="rounded-2xl bg-gradient-to-r from-indigo-500 via-violet-500 to-fuchsia-500 p-px shadow-lg shadow-indigo-100 focus-within:shadow-indigo-200">
          <div className="flex items-end gap-2 rounded-[15px] bg-white p-2">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              disabled={sending}
              rows={2}
              maxLength={8000}
              data-testid="operations-chat-input"
              aria-label="Message Operations AI"
              placeholder="Ask about the assistant, bookings, or an improvement…"
              className="max-h-36 min-h-12 flex-1 resize-y border-0 bg-transparent px-2 py-2 text-xs leading-relaxed text-slate-900 outline-none placeholder:text-slate-400 disabled:opacity-60"
            />
            <button
              type="submit"
              disabled={sending || !draft.trim()}
              data-testid="operations-chat-send"
              aria-label="Send message"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {sending ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </button>
          </div>
        </div>
        <div className="mt-2 flex items-center justify-between gap-3 text-[9px] text-slate-400">
          <span className="flex items-center gap-1"><CornerDownLeft className="h-3 w-3" /> Enter to send · Shift+Enter for a new line</span>
          <span>{draft.length}/8000</span>
        </div>
      </form>
    </section>
  );
}
