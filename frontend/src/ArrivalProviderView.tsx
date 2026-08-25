import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { Bell, BellOff, CheckCircle2, Clock3, DoorOpen, Loader2, MapPin, MessageCircle, Send, XCircle } from 'lucide-react';
import {
  ArrivalSession,
  closeArrivalSession,
  getAdminArrivalSession,
  listArrivalSessions,
  sendAdminArrivalMessage,
} from './api';
import { getArrivalSoundEnabled, playAirRaidSiren, setArrivalSoundEnabled } from './incomingMessageAlarm';
import PwaControls from './PwaControls';

function timeLabel(value: string | null) {
  if (!value) return '';
  return new Date(value).toLocaleString('en-AU', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
}

export default function ArrivalProviderView() {
  const [sessions, setSessions] = useState<ArrivalSession[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(() => new URLSearchParams(window.location.search).get('session'));
  const [selected, setSelected] = useState<ArrivalSession | null>(null);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [soundEnabled, setSoundEnabled] = useState(getArrivalSoundEnabled);

  const refresh = useCallback(async () => {
    try {
      const next = await listArrivalSessions();
      setSessions(next);
      setSelectedId(current => current || next.find(item => item.status === 'active' && !item.acknowledgedAt)?.id || next[0]?.id || null);
      setError('');
    } catch {
      setError('Could not refresh arrival chats.');
    }
  }, []);

  const refreshSelected = useCallback(async () => {
    if (!selectedId) { setSelected(null); return; }
    try { setSelected(await getAdminArrivalSession(selectedId)); } catch { setError('Could not load that chat.'); }
  }, [selectedId]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh();
    }, 6000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    void refreshSelected();
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refreshSelected();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [refreshSelected]);

  const activeCount = useMemo(() => sessions.filter(item => (
    item.status === 'active' && !item.acknowledgedAt
  )).length, [sessions]);

  const send = async (event: FormEvent) => {
    event.preventDefault();
    const message = text.trim();
    if (!selected || !message || sending) return;
    setSending(true);
    try {
      await sendAdminArrivalMessage(selected.id, message);
      setText('');
      await Promise.all([refresh(), refreshSelected()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Message failed to send.');
    } finally { setSending(false); }
  };

  const close = async () => {
    if (!selected || !window.confirm('Close this customer arrival chat?')) return;
    await closeArrivalSession(selected.id);
    await Promise.all([refresh(), refreshSelected()]);
  };

  const toggleSound = async () => {
    const next = !soundEnabled;
    setSoundEnabled(next);
    setArrivalSoundEnabled(next);
    if (next) {
      try { await playAirRaidSiren(undefined, 3500); } catch { setError('Your browser blocked sound. Tap Enable siren again.'); }
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-[#faf9f6] p-3 text-slate-800 sm:p-5">
      <header className="mb-4 flex shrink-0 flex-col items-stretch justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:flex-row sm:items-center">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600"><DoorOpen className="h-6 w-6" /></div>
          <div><h1 className="text-lg font-black">Customer arrivals</h1><p className="text-xs font-semibold text-slate-500">{activeCount} waiting now</p></div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <PwaControls onError={setError} />
          <button onClick={() => void toggleSound()} className={`flex flex-1 items-center justify-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-bold sm:flex-none ${soundEnabled ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-slate-200 text-slate-500'}`}>
            {soundEnabled ? <Bell className="h-4 w-4" /> : <BellOff className="h-4 w-4" />}
            {soundEnabled ? 'Siren on' : 'Enable siren'}
          </button>
          <button onClick={() => void refresh()} className="flex-1 rounded-xl border border-slate-200 px-3 py-2 text-xs font-bold sm:flex-none">Refresh</button>
        </div>
      </header>
      {error && <div className="mb-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-bold text-rose-700">{error}</div>}
      <div className="grid min-h-0 flex-1 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm md:grid-cols-[320px_1fr]">
        <aside className={`${selected ? 'hidden md:block' : 'block'} overflow-y-auto border-r border-slate-200`}>
          {sessions.length === 0 && <div className="p-10 text-center text-sm text-slate-400"><MapPin className="mx-auto mb-3 h-9 w-9 text-slate-200" />No arrival links yet.<br />Create one from Bookings.</div>}
          {sessions.map(item => (
            <button key={item.id} onClick={() => setSelectedId(item.id)} className={`w-full border-b border-slate-100 p-4 text-left hover:bg-slate-50 ${selectedId === item.id ? 'bg-indigo-50' : ''}`}>
              <div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-black">{item.booking.summary}</span><span className={`rounded-full px-2 py-0.5 text-[10px] font-black ${item.status === 'active' && !item.acknowledgedAt ? 'bg-emerald-100 text-emerald-700' : item.status === 'invited' ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-500'}`}>{item.status === 'active' ? (item.acknowledgedAt ? 'SEEN' : 'ARRIVED') : item.status.toUpperCase()}</span></div>
              <p className="mt-1 truncate text-xs text-slate-500">{item.booking.customerPhone || 'No phone'} · {timeLabel(item.booking.startTime)}</p>
              <p className="mt-1 text-[11px] text-slate-400">Updated {timeLabel(item.lastActivityAt)}</p>
            </button>
          ))}
        </aside>
        <section className={`${selected ? 'flex' : 'hidden md:flex'} min-h-0 flex-col`}>
          {!selected ? <div className="m-auto text-center text-sm text-slate-400"><MessageCircle className="mx-auto mb-3 h-10 w-10 text-slate-200" />Select an arrival chat</div> : <>
            <div className="flex items-center justify-between gap-3 border-b border-slate-200 p-4">
              <div className="min-w-0"><button onClick={() => { setSelected(null); setSelectedId(null); }} className="mb-1 text-xs font-bold text-indigo-600 md:hidden">← All arrivals</button><h2 className="truncate font-black">{selected.booking.summary}</h2><p className="text-xs text-slate-500">{selected.booking.customerPhone || 'No phone'} · {timeLabel(selected.booking.startTime)}</p></div>
              {selected.status === 'active' && <button onClick={() => void close()} className="flex shrink-0 items-center gap-1.5 rounded-xl border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600"><XCircle className="h-4 w-4" /> Close</button>}
            </div>
            <div className="flex-1 space-y-3 overflow-y-auto bg-slate-50 p-4">
              {selected.status === 'invited' && <div className="mx-auto flex w-fit items-center gap-2 rounded-full bg-amber-100 px-3 py-2 text-xs font-bold text-amber-800"><Clock3 className="h-4 w-4" /> Link sent, not yet activated</div>}
              {selected.activatedAt && <div className="mx-auto flex w-fit items-center gap-2 rounded-full bg-emerald-100 px-3 py-2 text-xs font-bold text-emerald-800"><CheckCircle2 className="h-4 w-4" /> Arrived {timeLabel(selected.activatedAt)}</div>}
              {selected.messages?.filter(message => message.sender !== 'system').map(message => (
                <div key={message.id} className={`flex ${message.sender === 'provider' ? 'justify-end' : 'justify-start'}`}><div className={`max-w-[82%] rounded-2xl px-4 py-3 text-sm shadow-sm ${message.sender === 'provider' ? 'rounded-br-md bg-indigo-600 text-white' : 'rounded-bl-md bg-white text-slate-800'}`}>{message.text}</div></div>
              ))}
            </div>
            {selected.status === 'active' ? <form onSubmit={send} className="flex gap-2 border-t border-slate-200 p-3"><input autoFocus value={text} onChange={event => setText(event.target.value)} maxLength={2000} placeholder="Wait time or entry instructions…" className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm outline-none focus:border-indigo-400" /><button type="submit" disabled={!text.trim() || sending} className="flex h-11 shrink-0 items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 font-bold text-white disabled:bg-slate-300">{sending ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}<span>Send</span></button></form> : <div className="border-t p-4 text-center text-xs font-bold text-slate-400">This chat is not active.</div>}
          </>}
        </section>
      </div>
    </div>
  );
}

