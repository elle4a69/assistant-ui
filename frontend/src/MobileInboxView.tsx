import { FormEvent, TouchEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft,
  CalendarPlus,
  Check,
  CheckCheck,
  DoorOpen,
  MessageCircle,
  RefreshCw,
  Search,
  Send,
  Wifi,
  X,
} from 'lucide-react'
import {
  getSettings,
  getThread,
  listArrivalSessions,
  listThreads,
  sendThreadReply,
  toggleAutoresponder,
  updateSettings,
  catchUpMissedMessage,
  approveDraft,
  discardDraft,
  respondToInformationRequest,
  acknowledgeThreadArrival,
  Message,
  ThreadDetail,
  ThreadListItem,
} from './api'
import { formatMessageTimestamp } from './messageTimestamp'
import { dismissArrivalPushNotification, stopIncomingAlarm } from './incomingMessageAlarm'

const POLL_THREADS_MS = 5000
const POLL_MESSAGES_MS = 3000

function formatListTime(value: string) {
  const date = new Date(value)
  const now = new Date()
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  }
  return date.toLocaleDateString([], { day: 'numeric', month: 'short' })
}

function contactLabel(phone: string) {
  if (phone.startsWith('locanto_')) {
    return phone.slice('locanto_'.length).replace(/[_-]+/g, ' ')
  }
  return phone
}

function avatarText(phone: string) {
  const label = contactLabel(phone).trim()
  if (label.startsWith('+')) return label.slice(-2)
  const words = label.split(/\s+/).filter(Boolean)
  return words.slice(0, 2).map(word => word[0]?.toUpperCase()).join('') || 'C'
}

interface MobileInboxViewProps {
  selectedId: string | null
  setSelectedId: (id: string | null) => void
}

export default function MobileInboxView({ selectedId, setSelectedId }: MobileInboxViewProps) {
  const [threads, setThreads] = useState<ThreadListItem[]>([])
  const [thread, setThread] = useState<ThreadDetail | null>(null)
  const [query, setQuery] = useState('')
  const [composer, setComposer] = useState('')
  const [aiEnabled, setAiEnabled] = useState(true)
  const [trainingEnabled, setTrainingEnabled] = useState(false)
  const [showAvatars, setShowAvatars] = useState<boolean | null>(null)
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [changingThreadAi, setChangingThreadAi] = useState(false)
  const [catchingUp, setCatchingUp] = useState(false)
  const [notice, setNotice] = useState('')
  const [reviewingDraftId, setReviewingDraftId] = useState<string | null>(null)
  const [requestedInformation, setRequestedInformation] = useState('')
  const [submittingInformation, setSubmittingInformation] = useState(false)
  const [error, setError] = useState('')
  const touchStartX = useRef<number | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const sendingRef = useRef(false)
  const reviewingDraftRef = useRef<string | null>(null)
  const selectedIdRef = useRef(selectedId)
  const threadsRequestRef = useRef(0)
  const threadRequestRef = useRef(0)
  const acknowledgedArrivalsRef = useRef(new Set<string>())
  const arrivalOpenIntentRef = useRef<{ threadId: string; sessionId: string } | null>(null)
  selectedIdRef.current = selectedId
  const loadThreads = useCallback(async () => {
    const requestId = ++threadsRequestRef.current
    try {
      const nextThreads = await listThreads()
      if (requestId !== threadsRequestRef.current) return
      setThreads(nextThreads)
      setError('')
    } catch {
      setError('Could not load messages')
    } finally {
      setLoading(false)
    }
  }, [])

  const clearArrivalUrl = useCallback(() => {
    const url = new URL(window.location.href)
    if (!url.searchParams.has('arrival')) return
    url.searchParams.delete('arrival')
    window.history.replaceState(null, '', `${url.pathname}${url.search}`)
  }, [])

  const acknowledgeArrival = useCallback(async (threadId: string, sessionId: string) => {
    if (acknowledgedArrivalsRef.current.has(sessionId)) return
    acknowledgedArrivalsRef.current.add(sessionId)
    try {
      await new Promise<void>(resolve => window.requestAnimationFrame(() => resolve()))
      await acknowledgeThreadArrival(threadId, sessionId)
      const sessions = await listArrivalSessions().catch(() => null)
      const remainingCount = sessions?.filter(item => (
        item.status === 'active' && !item.acknowledgedAt
      )).length
      if (remainingCount === 0) stopIncomingAlarm()
      await dismissArrivalPushNotification(sessionId, remainingCount)
      setThread(current => current?.id === threadId ? {
        ...current,
        pendingArrivalSessionId: null,
        pendingArrivalEventId: null,
        pendingArrivalAt: null,
      } : current)
      clearArrivalUrl()
      await loadThreads()
    } catch {
      acknowledgedArrivalsRef.current.delete(sessionId)
    }
  }, [clearArrivalUrl, loadThreads])

  const loadThread = useCallback(async () => {
    if (!selectedId) return
    const requestId = ++threadRequestRef.current
    try {
      const detail = await getThread(selectedId)
      if (requestId !== threadRequestRef.current || selectedIdRef.current !== selectedId) return
      setThread(detail)

      const requestedArrival = new URLSearchParams(window.location.search).get('arrival')
      const requestedArrivalMatches = Boolean(
        requestedArrival && requestedArrival === detail.pendingArrivalSessionId
      )
      if (requestedArrival && !requestedArrivalMatches) clearArrivalUrl()
      const openIntent = arrivalOpenIntentRef.current
      const intendedArrival = openIntent?.threadId === selectedId
        && openIntent.sessionId === detail.pendingArrivalSessionId
        ? openIntent.sessionId
        : null
      const arrivalSessionId = requestedArrivalMatches ? requestedArrival : intendedArrival
      if (
        arrivalSessionId
        && document.visibilityState === 'visible'
        && document.hasFocus()
      ) {
        arrivalOpenIntentRef.current = null
        await acknowledgeArrival(selectedId, arrivalSessionId)
      }
      setError('')
    } catch {
      setThread(null)
      setSelectedId(null)
      setError('That conversation is no longer available')
      await loadThreads()
    }
  }, [acknowledgeArrival, clearArrivalUrl, selectedId, loadThreads])

  useEffect(() => {
    let active = true
    let timeout: number | undefined
    const poll = async () => {
      await loadThreads()
      if (active) timeout = window.setTimeout(poll, POLL_THREADS_MS)
    }
    void poll()
    return () => {
      active = false
      if (timeout !== undefined) window.clearTimeout(timeout)
    }
  }, [loadThreads])

  useEffect(() => {
    let active = true
    const handleAvatarSettingChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ showMessageAvatars?: boolean }>).detail
      if (typeof detail?.showMessageAvatars === 'boolean') {
        setShowAvatars(detail.showMessageAvatars)
      }
    }
    window.addEventListener('message-avatar-setting-changed', handleAvatarSettingChanged)
    void getSettings().then(settings => {
      if (!active) return
      setAiEnabled(settings.autoReplyGlobalEnabled !== false)
      setTrainingEnabled(!!settings.trainingModeEnabled)
      setShowAvatars(settings.showMessageAvatars !== false)
    }).catch(() => {
      setShowAvatars(true)
      setError('Could not read settings')
    })
    return () => {
      active = false
      window.removeEventListener('message-avatar-setting-changed', handleAvatarSettingChanged)
    }
  }, [])

  useEffect(() => {
    if (!selectedId) {
      setThread(null)
      return
    }
    let active = true
    let timeout: number | undefined
    const poll = async () => {
      await loadThread()
      if (active) timeout = window.setTimeout(poll, POLL_MESSAGES_MS)
    }
    void poll()
    return () => {
      active = false
      threadRequestRef.current += 1
      if (timeout !== undefined) window.clearTimeout(timeout)
    }
  }, [selectedId, loadThread])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [thread?.events.length, thread?.messages.length])

  const filteredThreads = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return threads
    return threads.filter(item =>
      contactLabel(item.customerPhone).toLowerCase().includes(needle)
      || item.lastMessageText.toLowerCase().includes(needle)
    )
  }, [threads, query])

  const orderedTimeline = useMemo(() => {
    const items: Array<
      | { kind: 'message'; id: string; at: string; message: Message }
      | { kind: 'arrival'; id: string; at: string; event: ThreadDetail['events'][number] }
    > = []
    for (const message of thread?.messages ?? []) {
      items.push({ kind: 'message', id: message.id, at: message.at, message })
    }
    for (const event of thread?.events ?? []) {
      if (event.type === 'customer-arrived') {
        items.push({ kind: 'arrival', id: event.id, at: event.at, event })
      }
    }
    return items.sort((left, right) => {
      const timeDifference = new Date(left.at).getTime() - new Date(right.at).getTime()
      return timeDifference || left.id.localeCompare(right.id)
    })
  }, [thread?.events, thread?.messages])

  const pendingInformationRequest = useMemo(() => {
    if (thread?.state !== 'needs-review') return null
    return [...(thread?.events ?? [])].reverse().find(event =>
      (event.type === 'information-request' || event.type === 'catch-up-handoff')
      && event.meta?.status !== 'resolved'
    ) ?? null
  }, [thread?.state, thread?.events])

  const toggleAi = async () => {
    const nextValue = !aiEnabled
    setAiEnabled(nextValue)
    try {
      await updateSettings({
        autoReplyGlobalEnabled: nextValue,
      })
      setError('')
    } catch {
      setAiEnabled(!nextValue)
      setError('Could not change AI status')
    }
  }

  const toggleTraining = async () => {
    const nextValue = !trainingEnabled
    setTrainingEnabled(nextValue)
    try {
      await updateSettings({
        trainingModeEnabled: nextValue,
      })
      setError('')
    } catch {
      setTrainingEnabled(!nextValue)
      setError('Could not change Training Mode')
    }
  }

  const catchUpMissed = async () => {
    if (!aiEnabled || catchingUp) return
    setCatchingUp(true)
    setNotice('Checking missed messages…')
    setError('')
    let sent = 0
    let informationRequests = 0
    try {
      // Keep memory bounded by processing one model request at a time, while
      // continuing through the full queue instead of silently stopping at ten.
      const safetyLimit = 50
      let remaining = 0
      let reachedSafetyLimit = false
      for (let processed = 0; processed < safetyLimit; processed += 1) {
        const result = await catchUpMissedMessage()
        remaining = result.remaining
        if (!result.processed) break
        if (result.outcome === 'sent') sent += 1
        if (result.outcome === 'information-request') informationRequests += 1
        setNotice(`Catch-up: ${sent} sent, ${informationRequests} information request${informationRequests === 1 ? '' : 's'}, ${remaining} remaining`)
        if ((processed + 1) % 5 === 0) await loadThreads()
        await new Promise(resolve => window.setTimeout(resolve, 750))
        reachedSafetyLimit = processed === safetyLimit - 1 && remaining > 0
      }
      setNotice(
        reachedSafetyLimit
          ? `Catch-up paused after ${safetyLimit} conversations for safety. Click refresh again to continue.`
          : `Catch-up complete: ${sent} sent, ${informationRequests} information request${informationRequests === 1 ? '' : 's'}`,
      )
      await loadThreads()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not catch up missed messages')
    } finally {
      setCatchingUp(false)
    }
  }

  const reviewDraft = async (messageId: string, action: 'approve' | 'discard') => {
    if (reviewingDraftRef.current) return
    reviewingDraftRef.current = messageId
    setReviewingDraftId(messageId)
    setError('')
    try {
      if (action === 'approve') await approveDraft(messageId)
      else await discardDraft(messageId)
      await Promise.all([loadThread(), loadThreads()])
    } catch (err) {
      setError(err instanceof Error ? err.message : action === 'approve' ? 'Draft could not be sent' : 'Draft could not be discarded')
    } finally {
      reviewingDraftRef.current = null
      setReviewingDraftId(null)
    }
  }

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault()
    const text = composer.trim()
    if (!selectedId || !text || sendingRef.current) return
    sendingRef.current = true
    setComposer('')
    setSending(true)
    try {
      await sendThreadReply(selectedId, 'mobile-inbox', text, crypto.randomUUID())
      await Promise.all([loadThread(), loadThreads()])
      setError('')
    } catch (err) {
      setComposer(text)
      setError(err instanceof Error ? err.message : 'Message could not be sent')
    } finally {
      sendingRef.current = false
      setSending(false)
    }
  }

  const toggleThreadAi = async () => {
    if (!thread || changingThreadAi) return
    const enabled = !thread.autoReplyEnabled
    setChangingThreadAi(true)
    setThread(current => current ? { ...current, autoReplyEnabled: enabled } : current)
    try {
      const result = await toggleAutoresponder(thread.id, enabled)
      setThread(current => current ? { ...current, autoReplyEnabled: result.autoReplyEnabled } : current)
      await loadThreads()
      setError('')
    } catch {
      setThread(current => current ? { ...current, autoReplyEnabled: !enabled } : current)
      setError('Could not change AI for this conversation')
    } finally {
      setChangingThreadAi(false)
    }
  }

  const openBookingForThread = () => {
    if (!thread) return
    const params = new URLSearchParams({
      phone: thread.customerPhone,
      provider: thread.smsAccountKey === 'secondary' ? 'anonymous' : 'tori',
    })
    window.location.assign(`/booking?${params.toString()}`)
  }

  const answerInformationRequest = async () => {
    const information = requestedInformation.trim()
    if (!selectedId || !pendingInformationRequest || !information || submittingInformation) return
    setSubmittingInformation(true)
    setError('')
    try {
      const result = await respondToInformationRequest(
        selectedId,
        pendingInformationRequest.id,
        'mobile-inbox',
        information,
      )
      setRequestedInformation('')
      setNotice(`Knowledge saved to ${result.knowledgeSource}. Reply sent.`)
      await Promise.all([loadThread(), loadThreads()])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The information could not be used. No reply was sent.')
    } finally {
      setSubmittingInformation(false)
    }
  }

  const onTouchStart = (event: TouchEvent) => {
    touchStartX.current = event.changedTouches[0]?.clientX ?? null
  }

  const onTouchEnd = (event: TouchEvent) => {
    if (touchStartX.current === null) return
    const distance = (event.changedTouches[0]?.clientX ?? 0) - touchStartX.current
    touchStartX.current = null
    if (distance > 80) setSelectedId(null)
  }

  return (
    <div className="flex-1 w-full flex flex-col overflow-hidden bg-[#f4f6f8] text-slate-900">
      <div className="mx-auto flex h-full w-full max-w-2xl flex-col bg-white shadow-2xl shadow-slate-300/40">
        <header className="sticky top-0 z-30 flex min-h-14 shrink-0 items-center gap-1 border-b border-slate-200 bg-white px-2 pt-[env(safe-area-inset-top)] select-none">
          {/* Left: Back button + AI Toggle */}
          <div className="flex shrink-0 items-center justify-start gap-1">
            {selectedId && (
              <button
                type="button"
                onClick={() => setSelectedId(null)}
                className="-ml-1 grid h-11 w-11 shrink-0 place-items-center rounded-full text-slate-700 active:bg-slate-100 cursor-pointer border-none bg-transparent"
                aria-label="Back to conversations"
              >
                <ArrowLeft className="h-6 w-6" />
              </button>
            )}
            <label className="flex items-center gap-1.5 cursor-pointer">
              <span className={`text-[10px] font-extrabold uppercase tracking-wider ${aiEnabled ? 'text-emerald-700' : 'text-slate-400'}`}>
                AI
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={aiEnabled}
                onClick={toggleAi}
                className={`relative h-5 w-9 rounded-full transition-colors cursor-pointer border-none p-0 ${
                  aiEnabled ? 'bg-emerald-500' : 'bg-slate-300'
                }`}
              >
                <span className={`absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
                  aiEnabled ? 'translate-x-4' : 'translate-x-0'
                }`} />
              </button>
            </label>
          </div>

          {/* Center: compact search on the list, contact title inside a chat */}
          <div className="min-w-0 flex-1 px-1 text-center">
            {thread ? (
              <>
                <h1 className="truncate text-sm font-black tracking-tight text-slate-800">
                  {contactLabel(thread.customerPhone)}
                </h1>
                <p className="text-[9px] font-bold text-indigo-600 tracking-wider uppercase">
                  {thread.smsAccountKey === 'secondary' ? 'SMS line 2' : 'SMS line 1'} · Active Chat
                </p>
              </>
            ) : (
              <div className="flex h-8 min-w-20 items-center gap-1.5 rounded-lg bg-slate-100 px-2">
                <Search className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                <input
                  value={query}
                  onChange={event => setQuery(event.target.value)}
                  placeholder="Search"
                  aria-label="Search conversations"
                  className="min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-slate-400"
                />
              </div>
            )}
          </div>

          {/* Right: compact actions and Training Mode */}
          <div className="flex shrink-0 items-center justify-end gap-1">
            {!selectedId && (
              <button
                type="button"
                onClick={catchUpMissed}
                disabled={!aiEnabled || catchingUp}
                className="relative grid h-8 w-8 place-items-center rounded-full bg-slate-900 text-white active:bg-slate-700 disabled:bg-slate-300"
                title={notice || 'Catch up missed messages'}
                aria-label={catchingUp ? 'Catching up missed messages' : 'Catch up missed messages'}
              >
                <RefreshCw className={`h-3.5 w-3.5 ${catchingUp ? 'animate-spin' : ''}`} />
              </button>
            )}
            <label className="flex items-center gap-1.5 cursor-pointer">
              <span className={`text-[10px] font-extrabold uppercase tracking-wider ${trainingEnabled ? 'text-amber-600' : 'text-slate-400'}`}>
                Train
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={trainingEnabled}
                onClick={toggleTraining}
                className={`relative h-5 w-9 rounded-full transition-colors cursor-pointer border-none p-0 ${
                  trainingEnabled ? 'bg-amber-500' : 'bg-slate-300'
                }`}
              >
                <span className={`absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
                  trainingEnabled ? 'translate-x-4' : 'translate-x-0'
                }`} />
              </button>
            </label>
          </div>
        </header>

        <span className="sr-only" aria-live="polite">{notice}</span>

        {error && (
          <div className="shrink-0 bg-rose-50 px-4 py-2 text-center text-xs font-medium text-rose-700">
            {error}
          </div>
        )}

        {!selectedId ? (
          <section className="flex min-h-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
              {loading ? (
                <div className="space-y-1 px-4 py-2">
                  {[1, 2, 3, 4].map(item => (
                    <div key={item} className="flex animate-pulse gap-3 py-3">
                      {showAvatars === true && <div className="h-12 w-12 rounded-full bg-slate-200" />}
                      <div className="flex-1 space-y-2 py-1">
                        <div className="h-3 w-32 rounded bg-slate-200" />
                        <div className="h-3 w-4/5 rounded bg-slate-100" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : filteredThreads.length === 0 ? (
                <div className="grid h-full place-items-center px-8 text-center text-slate-400">
                  <div>
                    <MessageCircle className="mx-auto mb-3 h-11 w-11 stroke-[1.5]" />
                    <p className="text-sm font-semibold">No conversations yet</p>
                  </div>
                </div>
              ) : (
                filteredThreads.map(item => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => {
                      arrivalOpenIntentRef.current = item.pendingArrivalSessionId
                        ? { threadId: item.id, sessionId: item.pendingArrivalSessionId }
                        : null
                      setSelectedId(item.id)
                    }}
                    className={`flex w-full items-center border-b px-4 py-3 text-left active:bg-slate-50 ${showAvatars ? 'gap-3' : 'gap-0'} ${
                      item.pendingArrivalSessionId
                        ? 'border-emerald-300 bg-emerald-50 motion-safe:animate-pulse'
                        : 'border-slate-100'
                    }`}
                  >
                    {showAvatars && (
                      <div className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 text-sm font-bold text-white">
                        {avatarText(item.customerPhone)}
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="truncate text-[15px] font-semibold">{contactLabel(item.customerPhone)}</p>
                        <span className="shrink-0 rounded-full border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-[9px] font-black uppercase tracking-wide text-indigo-700">
                          {item.smsAccountKey === 'secondary' ? 'Line 2' : 'Line 1'}
                        </span>
                        {item.pendingArrivalSessionId && (
                          <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-600 px-2 py-0.5 text-[9px] font-black uppercase tracking-wide text-white shadow-sm">
                            <DoorOpen className="h-3 w-3" /> Arrived
                          </span>
                        )}
                        {(item.lastMessageRole === 'draft' || item.status === 'needs-review') && (
                          <span
                            title={item.lastMessageRole === 'draft' ? 'Unsent AI draft waiting for approval' : 'This conversation needs a human answer'}
                            aria-label={item.lastMessageRole === 'draft' ? 'Draft waiting for approval' : 'Conversation needs review'}
                            className="inline-flex shrink-0 items-center gap-1 rounded-full border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-[9px] font-black uppercase tracking-wide text-rose-700"
                          >
                            <span className="grid h-3.5 w-3.5 place-items-center rounded-full bg-rose-600 text-[10px] leading-none text-white">?</span>
                            {item.lastMessageRole === 'draft' ? 'Draft' : 'Review'}
                          </span>
                        )}
                        <time className="ml-auto shrink-0 text-[10px] text-slate-400">{formatListTime(item.lastMessageAt)}</time>
                      </div>
                      <div className="mt-0.5 flex items-center gap-2">
                        <p className={`truncate text-[13px] ${item.lastMessageRole === 'draft' ? 'font-semibold text-rose-700' : 'text-slate-500'}`}>
                          {item.lastMessageRole === 'draft'
                            ? 'Draft: '
                            : item.lastMessageRole !== 'customer' && item.lastMessageText
                              ? 'You: '
                              : ''}
                          {item.lastMessageText || 'New conversation'}
                        </p>
                        {item.unreadCount > 0 && (
                          <span className="ml-auto grid min-h-5 min-w-5 shrink-0 place-items-center rounded-full bg-emerald-500 px-1 text-[10px] font-bold text-white">
                            {item.unreadCount > 99 ? '99+' : item.unreadCount}
                          </span>
                        )}
                      </div>
                    </div>
                  </button>
                ))
              )}
            </div>
          </section>
        ) : (
          <section
            className="flex min-h-0 flex-1 flex-col bg-[#eef1f5]"
            onTouchStart={onTouchStart}
            onTouchEnd={onTouchEnd}
          >
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-4">
              <div className="mx-auto flex max-w-xl flex-col gap-2">
                {orderedTimeline.map(item => {
                  if (item.kind === 'arrival') {
                    const isPending = item.event.id === thread?.pendingArrivalEventId
                      && Boolean(thread.pendingArrivalSessionId)
                    return (
                      <div key={item.id} className="my-2 flex items-center justify-center">
                        <button
                          type="button"
                          disabled={!isPending}
                          onClick={() => {
                            if (thread?.pendingArrivalSessionId) {
                              void acknowledgeArrival(thread.id, thread.pendingArrivalSessionId)
                            }
                          }}
                          className={`inline-flex items-center gap-2 rounded-full border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs font-black text-emerald-800 shadow-sm ${isPending ? 'motion-safe:animate-pulse' : 'cursor-default'}`}
                        >
                          <DoorOpen className="h-4 w-4" />
                          <span>Client arrived at {formatMessageTimestamp(item.at)}{isPending ? ' · Tap to acknowledge' : ''}</span>
                        </button>
                      </div>
                    )
                  }
                  const message = item.message
                  const incoming = message.role === 'customer'
                  const isDraft = message.role === 'draft'
                  return (
                    <div
                      key={message.id}
                      className={`max-w-[82%] rounded-2xl px-3.5 py-2.5 shadow-sm ${
                        incoming
                          ? 'self-start rounded-bl-md bg-white text-slate-900'
                          : isDraft
                            ? 'self-end rounded-br-md border border-amber-300 bg-amber-50 text-slate-900'
                          : 'self-end rounded-br-md bg-emerald-600 text-white'
                      }`}
                    >
                      {isDraft && <p className="mb-1 text-[9px] font-black uppercase tracking-widest text-amber-700">Draft—not sent</p>}
                      <p className="whitespace-pre-wrap break-words text-[15px] leading-snug">{message.text}</p>
                      <div className={`mt-1 flex items-center justify-end gap-1 text-[9px] ${
                        incoming ? 'text-slate-400' : isDraft ? 'text-amber-700' : 'text-emerald-100'
                      }`}>
                        <time dateTime={message.at}>{formatMessageTimestamp(message.at)}</time>
                        {!incoming && !isDraft && <CheckCheck className="h-3 w-3" />}
                      </div>
                      {isDraft && (
                        <div className="mt-2 flex gap-2 border-t border-amber-200 pt-2">
                          <button
                            type="button"
                            onClick={() => reviewDraft(message.id, 'discard')}
                            disabled={reviewingDraftId === message.id}
                            className="flex min-h-9 flex-1 items-center justify-center gap-1 rounded-lg bg-white text-xs font-bold text-slate-600 disabled:opacity-50"
                          >
                            <X className="h-3.5 w-3.5" /> Discard
                          </button>
                          <button
                            type="button"
                            onClick={() => reviewDraft(message.id, 'approve')}
                            disabled={reviewingDraftId === message.id}
                            className="flex min-h-9 flex-1 items-center justify-center gap-1 rounded-lg bg-emerald-600 text-xs font-bold text-white disabled:opacity-50"
                          >
                            <Check className="h-3.5 w-3.5" /> Send
                          </button>
                        </div>
                      )}
                    </div>
                  )
                })}
                <div ref={endRef} />
              </div>
            </div>

            {pendingInformationRequest && (
              <div className="shrink-0 border-t border-amber-200 bg-amber-50 px-3 py-3">
                <div className="mx-auto max-w-xl rounded-2xl border border-amber-300 bg-white p-3 shadow-sm">
                  <p className="text-[10px] font-black uppercase tracking-widest text-amber-700">Information request</p>
                  <p className="mt-1 text-sm font-semibold text-slate-800">
                    Tori needs: {String(pendingInformationRequest.meta?.reason || 'More business information to answer this message safely.')}
                  </p>
                  <p className="mt-1 text-[11px] text-slate-500">Nothing has been sent to the customer. Give Tori the missing facts below.</p>
                  <textarea
                    value={requestedInformation}
                    onChange={event => setRequestedInformation(event.target.value)}
                    rows={3}
                    maxLength={6000}
                    placeholder="Type the information Tori needs..."
                    className="mt-2 w-full resize-none rounded-xl border border-slate-300 bg-slate-50 px-3 py-2 text-sm outline-none focus:border-amber-500 focus:bg-white"
                  />
                  <button
                    type="button"
                    onClick={answerInformationRequest}
                    disabled={!requestedInformation.trim() || submittingInformation}
                    className="mt-2 min-h-11 w-full rounded-xl bg-amber-600 px-4 text-sm font-bold text-white disabled:bg-slate-300"
                  >
                    {submittingInformation ? 'Tori is preparing the reply...' : 'Save knowledge and send reply'}
                  </button>
                </div>
              </div>
            )}

            <form onSubmit={sendMessage} className="shrink-0 border-t border-slate-200 bg-white px-3 pt-1 pb-0">
              <div className="relative mb-0.5 flex h-6 items-center px-1">
                <label className="flex items-center gap-1.5 text-[10px] font-extrabold text-slate-500">
                  AI replies
                  <button
                    type="button"
                    role="switch"
                    aria-checked={thread?.autoReplyEnabled ?? false}
                    disabled={changingThreadAi || !thread}
                    onClick={toggleThreadAi}
                    className={`relative h-4 w-7 rounded-full p-0 transition-colors ${thread?.autoReplyEnabled ? 'bg-emerald-500' : 'bg-slate-300'}`}
                  >
                    <span className={`absolute left-0.5 top-0.5 h-3 w-3 rounded-full bg-white shadow-sm transition-transform ${thread?.autoReplyEnabled ? 'translate-x-3' : 'translate-x-0'}`} />
                  </button>
                </label>
                <button
                  type="button"
                  onClick={openBookingForThread}
                  className="absolute left-1/2 flex h-6 -translate-x-1/2 items-center gap-1 rounded-md bg-indigo-50 px-2 text-[10px] font-extrabold text-indigo-700"
                >
                  <CalendarPlus className="h-3 w-3" /> Booking
                </button>
              </div>
              <div className="flex items-end gap-2">
                <textarea
                  value={composer}
                  onChange={event => setComposer(event.target.value)}
                  onKeyDown={event => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault()
                      event.currentTarget.form?.requestSubmit()
                    }
                  }}
                  rows={1}
                  placeholder="Message"
                  className="max-h-28 min-h-11 flex-1 resize-none rounded-3xl border border-slate-300 bg-slate-50 px-4 py-2.5 text-[15px] outline-none focus:border-emerald-500 focus:bg-white"
                />
                <button
                  type="submit"
                  disabled={!composer.trim() || sending}
                  className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-emerald-600 text-white shadow-sm disabled:bg-slate-300"
                  aria-label="Send message"
                >
                  {sending ? <Wifi className="h-4 w-4 animate-pulse" /> : <Send className="h-5 w-5" />}
                </button>
              </div>
            </form>
          </section>
        )}
      </div>
    </div>
  )
}

