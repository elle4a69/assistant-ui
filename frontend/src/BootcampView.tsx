import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  Bot,
  Check,
  ChevronLeft,
  Pause,
  Play,
  RotateCcw,
  Save,
  Square,
  Undo2,
  Users,
} from 'lucide-react';
import {
  applyBootcampProfile,
  BootcampConversation,
  BootcampPersona,
  BootcampRun,
  BootcampStyleProfile,
  controlBootcampRun,
  getBootcampPersonas,
  getBootcampProfile,
  getLatestBootcampRun,
  resetBootcampRuns,
  respondToBootcampInformationRequest,
  startBootcampRun,
  undoBootcampProfile,
} from './api';

const TRAITS: Array<{ key: keyof BootcampStyleProfile; label: string }> = [
  { key: 'flirtiness', label: 'Flirtiness' },
  { key: 'cheerfulness', label: 'Cheerfulness' },
  { key: 'wit', label: 'Wit' },
  { key: 'sarcasm', label: 'Sarcasm' },
  { key: 'warmth', label: 'Warmth' },
  { key: 'directness', label: 'Directness' },
  { key: 'chattiness', label: 'Chattiness' },
  { key: 'patience', label: 'Patience' },
];

const ACTIVE_STATUSES = new Set(['running', 'paused']);

function StatusPill({ status }: { status: string }) {
  const colour = status === 'running'
    ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
    : status === 'paused'
      ? 'bg-amber-500/15 text-amber-300 border-amber-500/30'
      : status === 'failed'
        ? 'bg-red-500/15 text-red-300 border-red-500/30'
        : 'bg-slate-700 text-slate-300 border-slate-600';
  return <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${colour}`}>{status}</span>;
}

function ConversationThread({
  conversation,
  onResolveInformationRequest,
  resolvingInformationRequest,
}: {
  conversation: BootcampConversation | null;
  onResolveInformationRequest: (conversationId: string, information: string) => Promise<void>;
  resolvingInformationRequest: boolean;
}) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const [information, setInformation] = useState('');
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [conversation?.messages.length]);
  useEffect(() => {
    setInformation('');
  }, [conversation?.id]);

  if (!conversation) {
    return (
      <div className="flex h-full min-h-64 items-center justify-center p-8 text-center text-sm text-slate-500">
        Select personas and start a run. Each simulated customer will keep an independent thread here.
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950">
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <div>
          <div className="font-bold text-white">{conversation.personaName}</div>
          <div className="text-[11px] text-slate-500">Turn {conversation.currentTurn}</div>
        </div>
        <StatusPill status={conversation.status} />
      </div>
      {conversation.needsHandoff && (
        <div className="m-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-100">
          <div className="flex gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <div>
              <strong>Information request. Nothing was sent.</strong>
              <div className="mt-1 text-amber-200/80">Tori needs: {conversation.handoffReason}</div>
            </div>
          </div>
          <textarea
            value={information}
            onChange={(event) => setInformation(event.target.value)}
            rows={3}
            maxLength={6000}
            placeholder="Give Tori the missing facts..."
            className="mt-3 w-full resize-none rounded-lg border border-amber-500/30 bg-slate-950/70 px-3 py-2 text-sm text-white outline-none placeholder:text-slate-500 focus:border-amber-400"
          />
          <button
            type="button"
            onClick={() => onResolveInformationRequest(conversation.id, information.trim())}
            disabled={!information.trim() || resolvingInformationRequest}
            className="mt-2 w-full rounded-lg bg-amber-500 px-3 py-2.5 text-xs font-black text-slate-950 disabled:bg-slate-700 disabled:text-slate-400"
          >
            {resolvingInformationRequest ? 'Tori is learning and retrying...' : 'Save lesson and retry this message'}
          </button>
        </div>
      )}
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {conversation.messages.map((message) => (
          <div key={message.id} className={`flex ${message.role === 'tori' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[84%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed shadow-sm ${
              message.role === 'tori'
                ? 'rounded-br-sm bg-indigo-600 text-white'
                : 'rounded-bl-sm border border-slate-700 bg-slate-800 text-slate-100'
            }`}>
              <div className="mb-1 text-[9px] font-bold uppercase tracking-widest opacity-60">
                {message.role === 'tori' ? 'Tori' : conversation.personaName}
              </div>
              {message.text}
            </div>
          </div>
        ))}
        {conversation.status === 'running' && (
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-400" />
            Thinking…
          </div>
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}

export default function BootcampView() {
  const [personas, setPersonas] = useState<BootcampPersona[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [profile, setProfile] = useState<BootcampStyleProfile | null>(null);
  const [run, setRun] = useState<BootcampRun | null>(null);
  const [activePersonaId, setActivePersonaId] = useState<string | null>(null);
  const [maxTurns, setMaxTurns] = useState(5);
  const [canUndo, setCanUndo] = useState(false);
  const [busy, setBusy] = useState(false);
  const [resolvingInformationRequest, setResolvingInformationRequest] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getBootcampPersonas(), getBootcampProfile(), getLatestBootcampRun()])
      .then(([loadedPersonas, loadedProfile, latest]) => {
        setPersonas(loadedPersonas);
        setSelected(loadedPersonas.slice(0, 4).map((item) => item.id));
        setProfile(loadedProfile.active);
        setCanUndo(loadedProfile.canUndo);
        setRun(latest);
        setActivePersonaId(latest?.conversations[0]?.personaId ?? null);
      })
      .catch((error) => setNotice(error.message));
  }, []);

  useEffect(() => {
    if (!run || !ACTIVE_STATUSES.has(run.status)) return;
    const timer = window.setInterval(() => {
      getLatestBootcampRun().then((latest) => {
        if (!latest) return;
        setRun(latest);
        setActivePersonaId((current) => current ?? latest.conversations[0]?.personaId ?? null);
      }).catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [run?.id, run?.status]);

  const activeConversation = useMemo(
    () => run?.conversations.find((item) => item.personaId === activePersonaId) ?? run?.conversations[0] ?? null,
    [run, activePersonaId],
  );
  const visiblePersonas = useMemo(
    () => run ? personas.filter((persona) => run.selectedPersonaIds.includes(persona.id)) : personas,
    [personas, run],
  );

  const togglePersona = (id: string) => {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  };

  const start = async () => {
    if (!profile || selected.length === 0) return;
    setBusy(true); setNotice(null);
    try {
      const next = await startBootcampRun(selected, maxTurns, profile);
      setRun(next);
      setActivePersonaId(selected[0]);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Unable to start Boot Camp');
    } finally { setBusy(false); }
  };

  const control = async (operation: 'pause' | 'resume' | 'stop') => {
    if (!run) return;
    setBusy(true);
    try { setRun(await controlBootcampRun(run.id, operation)); }
    catch (error) { setNotice(error instanceof Error ? error.message : 'Control failed'); }
    finally { setBusy(false); }
  };

  const reset = async () => {
    setBusy(true);
    try { await resetBootcampRuns(); setRun(null); setActivePersonaId(null); setNotice('Boot Camp threads cleared.'); }
    catch (error) { setNotice(error instanceof Error ? error.message : 'Reset failed'); }
    finally { setBusy(false); }
  };

  const applyProfile = async () => {
    if (!profile) return;
    setBusy(true);
    try {
      const result = await applyBootcampProfile(profile);
      setProfile(result.active); setCanUndo(result.canUndo); setNotice('This complete style profile is now active for Tori.');
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Apply failed'); }
    finally { setBusy(false); }
  };

  const undoProfile = async () => {
    setBusy(true);
    try {
      const result = await undoBootcampProfile();
      setProfile(result.active); setCanUndo(result.canUndo); setNotice('Previous Tori style restored.');
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Undo failed'); }
    finally { setBusy(false); }
  };

  const resolveInformationRequest = async (conversationId: string, information: string) => {
    if (!information || resolvingInformationRequest) return;
    setResolvingInformationRequest(true);
    setNotice(null);
    try {
      const result = await respondToBootcampInformationRequest(conversationId, information);
      const latest = await getLatestBootcampRun();
      if (latest) setRun(latest);
      setNotice(`Lesson saved to ${result.knowledgeSource}. Tori's corrected reply is now in this Boot Camp thread.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Tori could not use that information. Nothing was saved.');
    } finally {
      setResolvingInformationRequest(false);
    }
  };

  if (!profile) return <div className="flex h-full items-center justify-center bg-slate-950 text-slate-400">Loading Boot Camp…</div>;

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950 text-slate-100">
      <div className="border-b border-slate-800 bg-slate-900 px-3 py-3 sm:px-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="rounded-xl bg-indigo-500/15 p-2 text-indigo-300"><Bot className="h-5 w-5" /></div>
            <div><h1 className="font-bold">Tori Boot Camp</h1><p className="text-[11px] text-slate-400">Simulated only · paced updates · no SMS or bookings</p></div>
            {run && <StatusPill status={run.status} />}
          </div>
          <div className="flex flex-wrap gap-2">
            {!run || !ACTIVE_STATUSES.has(run.status) ? (
              <button onClick={start} disabled={busy || selected.length === 0} className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-bold disabled:opacity-40"><Play className="h-3.5 w-3.5" /> Start</button>
            ) : run.status === 'running' ? (
              <button onClick={() => control('pause')} disabled={busy} className="flex items-center gap-1.5 rounded-lg bg-amber-600 px-3 py-2 text-xs font-bold"><Pause className="h-3.5 w-3.5" /> Pause</button>
            ) : (
              <button onClick={() => control('resume')} disabled={busy} className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-bold"><Play className="h-3.5 w-3.5" /> Resume</button>
            )}
            {run && ACTIVE_STATUSES.has(run.status) && <button onClick={() => control('stop')} className="flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs font-bold"><Square className="h-3.5 w-3.5" /> Stop</button>}
            <button onClick={reset} disabled={busy || !!run && ACTIVE_STATUSES.has(run.status)} className="rounded-lg border border-slate-700 p-2 text-slate-300 disabled:opacity-30" title="Clear test threads"><RotateCcw className="h-4 w-4" /></button>
          </div>
        </div>
        {notice && <div className="mt-2 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-slate-200">{notice}</div>}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[250px_minmax(0,1fr)_310px]">
        <aside className="border-b border-slate-800 bg-slate-900 p-3 lg:overflow-y-auto lg:border-b-0 lg:border-r">
          <div className="mb-2 flex items-center justify-between text-xs font-bold text-slate-300"><span className="flex items-center gap-1.5"><Users className="h-4 w-4" /> {run ? `Threads ${run.conversations.length}/${run.selectedPersonaIds.length}` : 'Personas'}</span>{!run && <button onClick={() => setSelected(selected.length === personas.length ? [] : personas.map((item) => item.id))} className="text-[10px] text-indigo-300">{selected.length === personas.length ? 'Clear' : 'Select all'}</button>}</div>
          {run && (
            <select value={activeConversation?.personaId ?? ''} onChange={(event) => setActivePersonaId(event.target.value)} className="mb-2 w-full rounded-xl border border-slate-700 bg-slate-800 px-3 py-3 text-sm font-bold text-white lg:hidden">
              {run.conversations.map((conversation) => <option key={conversation.id} value={conversation.personaId}>{conversation.personaName} · {conversation.status}</option>)}
            </select>
          )}
          <div className={`${run ? 'hidden lg:flex' : 'flex'} gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible`}>
            {visiblePersonas.map((persona) => {
              const checked = selected.includes(persona.id);
              const conversation = run?.conversations.find((item) => item.personaId === persona.id);
              const active = activeConversation?.personaId === persona.id;
              return (
                <button key={persona.id} onClick={() => run ? setActivePersonaId(persona.id) : togglePersona(persona.id)} className={`min-w-44 rounded-xl border p-3 text-left transition lg:min-w-0 ${active ? 'border-indigo-500 bg-indigo-500/10' : checked ? 'border-slate-600 bg-slate-800' : 'border-slate-800 bg-slate-950/60 opacity-65'}`}>
                  <div className="flex items-center justify-between gap-2"><span className="text-xs font-bold">{persona.name}</span>{!run && checked && <Check className="h-3.5 w-3.5 text-indigo-400" />}{conversation?.needsHandoff && <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />}</div>
                  <div className="mt-1 text-[10px] uppercase tracking-wider text-slate-500">{persona.category}</div>
                  <p className="mt-1 line-clamp-2 text-[11px] leading-snug text-slate-400">{persona.description}</p>
                </button>
              );
            })}
          </div>
          {!run && <div className="mt-3 flex items-center gap-3 text-xs"><label className="text-slate-400">Turns</label><input type="range" min="2" max="12" value={maxTurns} onChange={(event) => setMaxTurns(Number(event.target.value))} className="flex-1 accent-indigo-500" /><span className="w-5 text-right font-bold">{maxTurns}</span></div>}
        </aside>

        <main className="min-h-[420px] min-w-0 overflow-hidden">
          <ConversationThread
            conversation={activeConversation}
            onResolveInformationRequest={resolveInformationRequest}
            resolvingInformationRequest={resolvingInformationRequest}
          />
        </main>

        <aside className="overflow-y-auto border-t border-slate-800 bg-slate-900 p-4 lg:border-l lg:border-t-0">
          <div className="mb-1 text-sm font-bold">Tori style laboratory</div>
          <p className="mb-4 text-[11px] leading-relaxed text-slate-400">These values affect Boot Camp only until you deliberately apply the complete combination.</p>
          <div className="space-y-3.5">
            {TRAITS.map(({ key, label }) => (
              <label key={key} className="block">
                <div className="mb-1 flex justify-between text-xs"><span className="text-slate-300">{label}</span><strong className="text-indigo-300">{profile[key]}/5</strong></div>
                <input type="range" min="0" max="5" step="1" value={profile[key]} onChange={(event) => setProfile({ ...profile, [key]: Number(event.target.value) })} className="w-full accent-indigo-500" />
              </label>
            ))}
          </div>
          <div className="mt-5 grid grid-cols-2 gap-2">
            <button onClick={applyProfile} disabled={busy} className="flex items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2.5 text-xs font-bold"><Save className="h-3.5 w-3.5" /> Apply to Tori</button>
            <button onClick={undoProfile} disabled={busy || !canUndo} className="flex items-center justify-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2.5 text-xs font-bold disabled:opacity-35"><Undo2 className="h-3.5 w-3.5" /> Undo</button>
          </div>
          <div className="mt-3 flex gap-2 rounded-lg bg-slate-950 p-2.5 text-[10px] leading-relaxed text-slate-500"><ChevronLeft className="mt-0.5 h-3 w-3 shrink-0" />Applying saves one tested profile and preserves the previous profile for restoration.</div>
        </aside>
      </div>
    </div>
  );
}
