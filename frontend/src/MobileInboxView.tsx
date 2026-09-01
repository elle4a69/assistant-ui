import { FormEvent, TouchEvent, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft,
  Check,
  CheckCheck,
  DoorOpen,
  MessageCircle,
  Ban,
  Pin,
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
  setThreadBlocked,
  setThreadPinned,
  updateSettings,
  catchUpMissedMessage,
  approveDraft,
  discardDraft,
  updateDraft,
  respondToInformationRequest,
  acknowledgeThreadArrival,
  Message,
  ThreadDetail,
  ThreadListItem,
} from './api'
import { formatMessageTimestamp } from './messageTimestamp'
import { dismissArrivalPushNotification, stopIncomingAlarm } from './incomingMessageAlarm'
import QuickToolsSheet from './QuickToolsSheet'

const POLL_THREADS_MS = 10_000
const POLL_MESSAGES_MS = 6_000
const BACKGROUND_POLL_MS = 30_000

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
  const [changingPinned, setChangingPinned] = useState(false)
  const [changingBlocked, setChangingBlocked] = useState(false)
  const [catchingUp, setCatchingUp] = useState(false)
  const [notice, setNotice] = useState('')
  const [reviewingDraftId, setReviewingDraftId] = useState<string | null>(null)
  const [editingDraftId, setEditingDraftId] = useState<string | null>(null)
  const [editingDraftText, setEditingDraftText] = useState('')
  const [savingDraft, setSavingDraft] = useState(false)
  const [requestedInformation, setRequestedInformation] = useState('')
  const [submittingInformation, setSubmittingInformation] = useState(false)
  const [quickToolsOpen, setQuickToolsOpen] = useState(false)
  const [error, setError] = useState('')
  const touchStartX = useRef<number | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const sendingRef = useRef(false)
  const reviewingDraftRef = useRef<string | null>(null)
  const selectedIdRef = useRef(selectedId)
  const threadsRequestRef = useRef(0)
  const threadRequestRef = useRef(0)
  const acknowledgedArrivalsRef = useRef(new Set<string>())
  const arrivalOpenIntentRef = useRef<{ threadId: string; sessionId: string } | null>(null)
  selectedIdRef.current = selectedId

  useLayoutEffect(() => {
    const textarea = composerRef.current
    if (!textarea) return
    const maximumHeight = 144
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, maximumHeight)}px`
    textarea.style.overflowY = textarea.scrollHeight > maximumHeight ? 'auto' : 'hidden'
  }, [composer])

  const loadThreads = useCallback(async () => {
    const requestId = ++threadsRequestRef.current
    try {
      // The list payload contains only the latest message, so searching it locally
      // misses matches earlier in a conversation. Let the API search every message.
      const nextThreads = await listThreads({ search: query.trim() || undefined })
      if (requestId !== threadsRequestRef.current) return
      setThreads(nextThreads)
      setError('')
    } catch {
      setError('Could not load messages')
    } finally {
      setLoading(false)
    }
  }, [query])

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
      if (document.visibilityState === 'visible') await loadThreads()
      if (active) {
        timeout = window.setTimeout(
          poll,
          document.visibilityState === 'visible' ? POLL_THREADS_MS : BACKGROUND_POLL_MS,
        )
      }
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
      if (document.visibilityState === 'visible') await loadThread()
      if (active) {
        timeout = window.setTimeout(
          poll,
          document.visibilityState === 'visible' ? POLL_MESSAGES_MS : BACKGROUND_POLL_MS,
        )
      }
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
    // Search results are already filtered by the server across the full thread.
    return threads
  }, [threads])

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

  const saveDraftEdit = async (messageId: string) => {
    const text = editingDraftText.trim()
    if (!text || savingDraft) return
    setSavingDraft(true)
    setError('')
    try {
      await updateDraft(messageId, text)
      setEditingDraftId(null)
      setEditingDraftText('')
      await loadThread()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Draft could not be updated')
    } finally {
      setSavingDraft(false)
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

  const togglePinned = async () => {
    if (!thread || changingPinned) return
    const pinned = !thread.pinned
    setChangingPinned(true)
    try {
      const result = await setThreadPinned(thread.id, pinned)
      setThread(current => current ? { ...current, pinned: result.pinned } : current)
      await loadThreads()
      setError('')
    } catch {
      setError('Could not change the pin for this conversation')
    } finally {
      setChangingPinned(false)
    }
  }

  const toggleBlocked = async () => {
    if (!thread || changingBlocked) return
    const blocked = !thread.blocked
    if (blocked && !window.confirm(`Block ${contactLabel(thread.customerPhone)} on ${thread.smsAccountKey === 'secondary' ? 'SMS line 2' : 'SMS line 1'}? Automated replies will stop for this contact.`)) return
    setChangingBlocked(true)
    try {
      const result = await setThreadBlocked(thread.id, blocked)
      setThread(current => current ? { ...current, blocked: result.blocked } : current)
      await loadThreads()
      setNotice(result.blocked ? 'Contact blocked. Automated replies are suppressed.' : 'Contact unblocked.')
      setError('')
    } catch {
      setError(`Could not ${blocked ? 'block' : 'unblock'} this contact`)
    } finally {
      setChangingBlocked(false)
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

  const insertQuickText = (content: string) => {
    const textarea = composerRef.current
    const selectionStart = textarea?.selectionStart ?? composer.length
    const selectionEnd = textarea?.selectionEnd ?? composer.length
    setComposer(current => {
      const start = Math.min(selectionStart, current.length)
      const end = Math.min(selectionEnd, current.length)
      const before = current.slice(0, start)
      const after = current.slice(end)
      const prefix = before && !/\s$/.test(before) ? '\n' : ''
      const suffix = after && !/^\s/.test(after) ? '\n' : ''
      const next = `${before}${prefix}${content}${suffix}${after}`
      const caret = before.length + prefix.length + content.length
      window.requestAnimationFrame(() => {
        textarea?.focus()
        textarea?.setSelectionRange(caret, caret)
      })
      return next
    })
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
          {/* Left: keep Back isolated from settings controls */}
          <div className="flex shrink-0 items-center justify-start">
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

          {/* Right: global AI controls, safely away from Back */}
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
            <button
              type="button"
              aria-pressed={aiEnabled}
              onClick={toggleAi}
              className={`h-8 rounded-lg border px-2 text-[9px] font-extrabold transition active:scale-95 ${aiEnabled ? 'border-emerald-500 bg-emerald-500 text-white' : 'border-slate-300 bg-white text-slate-500'}`}
            >
              {aiEnabled ? 'AI On' : 'AI Off'}
            </button>
            <button
              type="button"
              aria-pressed={trainingEnabled}
              onClick={toggleTraining}
              className={`h-8 rounded-lg border px-2 text-[9px] font-extrabold transition active:scale-95 ${trainingEnabled ? 'border-amber-500 bg-amber-500 text-white' : 'border-slate-300 bg-white text-slate-500'}`}
            >
              {trainingEnabled ? 'Train On' : 'Train Off'}
            </button>
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
                        {item.pinned && <Pin className="h-3.5 w-3.5 shrink-0 fill-indigo-600 text-indigo-600" aria-label="Pinned" />}
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
                        {item.blocked && (
                          <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-rose-100 px-1.5 py-0.5 text-[9px] font-black uppercase text-rose-700">
                            <Ban className="h-3 w-3" /> Blocked
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
                      {isDraft && editingDraftId === message.id ? (
                        <textarea
                          value={editingDraftText}
                          onChange={event => setEditingDraftText(event.target.value)}
                          className="min-h-24 w-full rounded-lg border border-amber-300 bg-white p-2 text-[15px] leading-snug text-slate-900"
                          aria-label="Edit draft reply"
                        />
                      ) : (
                        <p className="whitespace-pre-wrap break-words text-[15px] leading-snug">{message.text}</p>
                      )}
                      <div className={`mt-1 flex items-center justify-end gap-1 text-[9px] ${
                        incoming ? 'text-slate-400' : isDraft ? 'text-amber-700' : 'text-emerald-100'
                      }`}>
                        <time dateTime={message.at}>{formatMessageTimestamp(message.at)}</time>
                        {!incoming && !isDraft && <CheckCheck className="h-3 w-3" />}
                      </div>
                      {isDraft && (
                        <div className="mt-2 flex gap-2 border-t border-amber-200 pt-2">
                          {editingDraftId === message.id ? (
                            <>
                              <button
                                type="button"
                                onClick={() => { setEditingDraftId(null); setEditingDraftText('') }}
                                disabled={savingDraft}
                                className="flex min-h-9 flex-1 items-center justify-center rounded-lg bg-white text-xs font-bold text-slate-600 disabled:opacity-50"
                              >
                                Cancel
                              </button>
                              <button
                                type="button"
                                onClick={() => saveDraftEdit(message.id)}
                                disabled={savingDraft || !editingDraftText.trim()}
                                className="flex min-h-9 flex-1 items-center justify-center rounded-lg bg-amber-600 text-xs font-bold text-white disabled:opacity-50"
                              >
                                Save edit
                              </button>
                            </>
                          ) : <>
                          <button
                            type="button"
                            onClick={() => { setEditingDraftId(message.id); setEditingDraftText(message.text) }}
                            disabled={reviewingDraftId === message.id}
                            className="flex min-h-9 flex-1 items-center justify-center rounded-lg bg-white text-xs font-bold text-slate-600 disabled:opacity-50"
                          >
                            Edit
                          </button>
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
                          </>}
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
              <div className="flex items-end gap-2">
                <textarea
                  ref={composerRef}
                  data-testid="message-composer"
                  value={composer}
                  onChange={event => setComposer(event.target.value)}
                  enterKeyHint="enter"
                  rows={1}
                  placeholder="Message"
                  className="max-h-36 min-h-11 flex-1 resize-none overflow-y-hidden rounded-3xl border border-slate-300 bg-slate-50 px-4 py-2.5 text-[15px] outline-none focus:border-emerald-500 focus:bg-white"
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
              <div className="mt-1 grid grid-cols-5 gap-1" aria-label="Conversation controls">
                <button
                  type="button"
                  aria-pressed={thread?.autoReplyEnabled ?? false}
                  aria-label={thread?.autoReplyEnabled ? 'Turn AI replies off' : 'Turn AI replies on'}
                  disabled={changingThreadAi || !thread || thread.blocked}
                  onClick={toggleThreadAi}
                  className={`h-7 rounded-md border px-1 text-[9px] font-extrabold transition active:scale-95 disabled:opacity-40 ${thread?.autoReplyEnabled ? 'border-emerald-500 bg-emerald-500 text-white' : 'border-slate-300 bg-white text-slate-600'}`}
                >
                  {changingThreadAi ? '…' : thread?.autoReplyEnabled ? 'AI On' : 'AI Off'}
                </button>
                <button
                  type="button"
                  onClick={() => setQuickToolsOpen(true)}
                  className="h-7 rounded-md border border-slate-900 bg-slate-900 px-1 text-[9px] font-extrabold text-white active:scale-95"
                  aria-label="Open quick tools"
                >
                  Tools
                </button>
                <button
                  type="button"
                  onClick={openBookingForThread}
                  className="h-7 rounded-md border border-indigo-200 bg-indigo-50 px-1 text-[9px] font-extrabold text-indigo-700 active:scale-95"
                >
                  Booking
                </button>
                <button
                  type="button"
                  onClick={togglePinned}
                  disabled={changingPinned || !thread}
                  aria-pressed={thread?.pinned ?? false}
                  aria-label={thread?.pinned ? 'Unpin conversation' : 'Pin conversation'}
                  className={`h-7 rounded-md border px-1 text-[9px] font-extrabold transition active:scale-95 disabled:opacity-50 ${thread?.pinned ? 'border-indigo-500 bg-indigo-500 text-white' : 'border-slate-300 bg-white text-slate-600'}`}
                >
                  {changingPinned ? '…' : thread?.pinned ? 'Unpin' : 'Pin'}
                </button>
                <button
                  type="button"
                  onClick={toggleBlocked}
                  disabled={changingBlocked || !thread}
                  aria-pressed={thread?.blocked ?? false}
                  aria-label={thread?.blocked ? 'Unblock contact' : 'Block contact'}
                  className={`h-7 rounded-md border px-1 text-[9px] font-extrabold transition active:scale-95 disabled:opacity-50 ${thread?.blocked ? 'border-rose-500 bg-rose-500 text-white' : 'border-slate-300 bg-white text-slate-600'}`}
                >
                  {changingBlocked ? '…' : thread?.blocked ? 'Unblock' : 'Block'}
                </button>
              </div>
            </form>
          </section>
        )}
        {thread && (
          <QuickToolsSheet
            open={quickToolsOpen}
            accountKey={thread.smsAccountKey}
            onClose={() => setQuickToolsOpen(false)}
            onInsert={insertQuickText}
          />
        )}
      </div>
    </div>
  )
}

