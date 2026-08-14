import { FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, Clock3, Loader2, MapPin, Send, Volume2, VolumeX } from 'lucide-react';
import {
  activateArrival,
  ArrivalSession,
  getClientArrivalSession,
  sendClientArrivalMessage,
} from './api';

const SESSION_ID_KEY = 'arrival.sessionId';
const SESSION_TOKEN_KEY = 'arrival.clientToken';

function fragmentInvite(): string {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  return params.get('invite') || '';
}

export default function ArrivalClientView() {
  const [session, setSession] = useState<ArrivalSession | null>(null);
  const [activating, setActivating] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [text, setText] = useState('');
  const [speakReplies, setSpeakReplies] = useState(false);
  const seenProviderMessages = useRef(new Set<string>());
  const invite = fragmentInvite();

  const credentials = useCallback(() => ({
    id: window.sessionStorage.getItem(SESSION_ID_KEY) || '',
    token: window.sessionStorage.getItem(SESSION_TOKEN_KEY) || '',
  }), []);

  const refresh = useCallback(async () => {
    const { id, token } = credentials();
    if (!id || !token) return;
    try {
      const next = await getClientArrivalSession(id, token);
      setSession(next);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'This arrival chat is no longer available.');
      window.sessionStorage.removeItem(SESSION_ID_KEY);
      window.sessionStorage.removeItem(SESSION_TOKEN_KEY);
    }
  }, [credentials]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!session?.messages) return;
    for (const message of session.messages) {
      if (message.sender === 'provider' && !seenProviderMessages.current.has(message.id)) {
        if (speakReplies && 'speechSynthesis' in window) {
          window.speechSynthesis.speak(new SpeechSynthesisUtterance(message.text));
        }
        seenProviderMessages.current.add(message.id);
      }
    }
  }, [session, speakReplies]);

  const activate = async () => {
    if (!invite || activating) return;
    setActivating(true);
    setError('');
    try {
      const result = await activateArrival(invite);
      window.sessionStorage.setItem(SESSION_ID_KEY, result.session.id);
      window.sessionStorage.setItem(SESSION_TOKEN_KEY, result.clientToken);
      window.history.replaceState(null, '', '/arrival');
      setSession(result.session);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'This arrival link could not be used.');
    } finally {
      setActivating(false);
    }
  };

  const send = async (event: FormEvent) => {
    event.preventDefault();
    const message = text.trim();
    const { id, token } = credentials();
    if (!message || !id || !token || sending) return;
    setSending(true);
    setError('');
    try {
      await sendClientArrivalMessage(id, token, message);
      setText('');
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Message failed to send.');
    } finally {
      setSending(false);
    }
  };

  if (!session) {
    return (
      <main className="min-h-[100dvh] w-full bg-slate-950 px-5 py-8 text-white flex items-center justify-center">
        <section className="w-full max-w-md rounded-3xl border border-white/10 bg-slate-900 p-6 shadow-2xl text-center">
          <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-indigo-500/15 text-indigo-300">
            <MapPin className="h-8 w-8" />
          </div>
          <h1 className="text-2xl font-black">Welcome</h1>
          <p className="mt-2 text-sm leading-6 text-slate-400">Press the button only when you have arrived. It works once and then opens your private chat.</p>
          {error && <p className="mt-5 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-200">{error}</p>}
          <button
            type="button"
            onClick={activate}
            disabled={!invite || activating}
            className="mt-7 flex min-h-28 w-full items-center justify-center gap-3 rounded-3xl bg-emerald-500 px-6 text-2xl font-black text-slate-950 shadow-lg shadow-emerald-950/40 transition active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
          >
            {activating ? <Loader2 className="h-8 w-8 animate-spin" /> : <CheckCircle2 className="h-8 w-8" />}
            {activating ? 'Checking in…' : "I’ve arrived"}
          </button>
          {!invite && !error && <p className="mt-4 text-xs text-slate-500">Open the private link supplied with your booking.</p>}
        </section>
      </main>
    );
  }

  const active = session.status === 'active';
  return (
    <main className="flex h-[100dvh] w-full flex-col bg-slate-100 text-slate-900">
      <header className="bg-slate-950 px-4 py-4 text-white shadow-md">
        <div className="mx-auto flex max-w-2xl items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-black"><span className="h-2.5 w-2.5 animate-pulse rounded-full bg-emerald-400" /> You’re checked in</div>
            <p className="mt-1 truncate text-xs text-slate-400">{session.booking.summary}</p>
          </div>
          <button onClick={() => setSpeakReplies(value => !value)} className="rounded-xl border border-white/10 bg-white/5 p-2.5 text-slate-200" title="Read replies aloud">
            {speakReplies ? <Volume2 className="h-5 w-5" /> : <VolumeX className="h-5 w-5" />}
          </button>
        </div>
      </header>
      <section className="mx-auto flex min-h-0 w-full max-w-2xl flex-1 flex-col">
        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          <div className="mx-auto flex w-fit items-center gap-2 rounded-full bg-white px-3 py-1.5 text-xs font-bold text-slate-500 shadow-sm">
            <Clock3 className="h-3.5 w-3.5" /> We know you’re here
          </div>
          {session.messages?.filter(message => message.sender !== 'system').map(message => (
            <div key={message.id} className={`flex ${message.sender === 'client' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-5 shadow-sm ${message.sender === 'client' ? 'rounded-br-md bg-indigo-600 text-white' : 'rounded-bl-md bg-white text-slate-800'}`}>
                {message.text}
              </div>
            </div>
          ))}
          {!session.messages?.some(message => message.sender === 'provider') && (
            <p className="px-5 py-8 text-center text-sm text-slate-400">Hang tight. You can send a message here while you wait for instructions.</p>
          )}
          {error && <p className="rounded-xl bg-rose-50 p-3 text-center text-sm text-rose-700">{error}</p>}
        </div>
        {active ? (
          <form onSubmit={send} className="flex gap-2 border-t border-slate-200 bg-white p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
            <input value={text} onChange={event => setText(event.target.value)} maxLength={2000} placeholder="Type a message…" className="min-w-0 flex-1 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-base outline-none focus:border-indigo-400" />
            <button disabled={!text.trim() || sending} className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-indigo-600 text-white disabled:bg-slate-300"><Send className="h-5 w-5" /></button>
          </form>
        ) : <div className="border-t bg-white p-4 text-center text-sm font-bold text-slate-500">This chat has ended.</div>}
      </section>
    </main>
  );
}
