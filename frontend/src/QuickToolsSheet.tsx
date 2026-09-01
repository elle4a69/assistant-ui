import { PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from 'react'
import {
  CalendarDays,
  ChevronLeft,
  Clock3,
  Save,
  Sparkles,
  X,
} from 'lucide-react'
import {
  FreeBusySlot,
  getFreeBusy,
  getQuickReplies,
  getServices,
  QuickReply,
  saveQuickReply,
  Service,
} from './api'

type AccountKey = 'primary' | 'secondary'
type SheetView = 'tools' | 'edit' | 'calendar'

const DEFAULT_REPLIES: QuickReply[] = [
  { label: 'ADDR', content: '' },
  { label: 'LINK', content: '' },
  { label: 'INFO', content: '' },
  { label: 'TEXT 4', content: '' },
  { label: 'TEXT 5', content: '' },
]
const MELBOURNE_TIME_ZONE = 'Australia/Melbourne'
const LONG_PRESS_MS = 520

interface QuickToolsSheetProps {
  open: boolean
  accountKey: AccountKey
  onClose: () => void
  onInsert: (content: string) => void
}

interface SlotGroup {
  key: string
  label: string
  slots: Array<{ id: string; label: string }>
}

function groupAvailability(slots: FreeBusySlot[]): SlotGroup[] {
  const dateKey = new Intl.DateTimeFormat('en-CA', {
    timeZone: MELBOURNE_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
  const dateLabel = new Intl.DateTimeFormat('en-AU', {
    timeZone: MELBOURNE_TIME_ZONE,
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
  const timeLabel = new Intl.DateTimeFormat('en-AU', {
    timeZone: MELBOURNE_TIME_ZONE,
    hour: 'numeric',
    minute: '2-digit',
  })
  const groups = new Map<string, SlotGroup>()
  slots.forEach(slot => {
    const start = new Date(slot.startTime)
    const key = dateKey.format(start)
    const group = groups.get(key) ?? { key, label: dateLabel.format(start), slots: [] }
    group.slots.push({ id: slot.startTime, label: timeLabel.format(start) })
    groups.set(key, group)
  })
  return [...groups.values()].sort((left, right) => left.key.localeCompare(right.key)).slice(0, 8)
}

export default function QuickToolsSheet({ open, accountKey, onClose, onInsert }: QuickToolsSheetProps) {
  const [view, setView] = useState<SheetView>('tools')
  const [replies, setReplies] = useState<QuickReply[]>(DEFAULT_REPLIES)
  const [repliesLoading, setRepliesLoading] = useState(false)
  const [editingIndex, setEditingIndex] = useState(0)
  const [draftLabel, setDraftLabel] = useState('')
  const [draftContent, setDraftContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [services, setServices] = useState<Service[]>([])
  const [selectedServiceId, setSelectedServiceId] = useState('')
  const [availability, setAvailability] = useState<FreeBusySlot[]>([])
  const [calendarLoading, setCalendarLoading] = useState(false)
  const [error, setError] = useState('')
  const pressTimerRef = useRef<number | null>(null)
  const longPressedIndexRef = useRef<number | null>(null)

  const lineServices = useMemo(
    () => services.filter(service => !service.lineKey || service.lineKey === accountKey),
    [accountKey, services],
  )
  const selectedService = lineServices.find(service => service.id === selectedServiceId) ?? null
  const availabilityGroups = useMemo(() => groupAvailability(availability), [availability])

  useEffect(() => {
    if (!open) return
    let active = true
    setView('tools')
    setError('')
    setRepliesLoading(true)
    getQuickReplies(accountKey)
      .then(items => {
        if (active) setReplies(items.length === 5 ? items : DEFAULT_REPLIES)
      })
      .catch(() => {
        if (active) setError('Quick buttons could not be loaded.')
      })
      .finally(() => {
        if (active) setRepliesLoading(false)
      })
    return () => { active = false }
  }, [accountKey, open])

  useEffect(() => {
    if (!open || view !== 'calendar' || !selectedService) return
    let active = true
    setCalendarLoading(true)
    setError('')
    setAvailability([])
    getFreeBusy(selectedService.duration)
      .then(slots => {
        if (active) setAvailability(slots)
      })
      .catch(() => {
        if (active) setError('Live availability could not be loaded. Try again.')
      })
      .finally(() => {
        if (active) setCalendarLoading(false)
      })
    return () => { active = false }
  }, [open, selectedService, view])

  useEffect(() => () => {
    if (pressTimerRef.current !== null) window.clearTimeout(pressTimerRef.current)
  }, [])

  if (!open) return null

  const editReply = (index: number) => {
    const reply = replies[index] ?? DEFAULT_REPLIES[index]
    setEditingIndex(index)
    setDraftLabel(reply.label)
    setDraftContent(reply.content)
    setError('')
    setView('edit')
  }

  const startLongPress = (event: ReactPointerEvent<HTMLButtonElement>, index: number) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return
    if (pressTimerRef.current !== null) window.clearTimeout(pressTimerRef.current)
    longPressedIndexRef.current = null
    pressTimerRef.current = window.setTimeout(() => {
      longPressedIndexRef.current = index
      editReply(index)
      if ('vibrate' in navigator) navigator.vibrate?.(20)
    }, LONG_PRESS_MS)
  }

  const endLongPress = () => {
    if (pressTimerRef.current !== null) window.clearTimeout(pressTimerRef.current)
    pressTimerRef.current = null
  }

  const useReply = (index: number) => {
    if (longPressedIndexRef.current === index) {
      longPressedIndexRef.current = null
      return
    }
    const reply = replies[index]
    if (!reply?.content) {
      editReply(index)
      return
    }
    onInsert(reply.content)
    onClose()
  }

  const persistReply = async () => {
    const label = draftLabel.trim()
    if (!label || saving) return
    setSaving(true)
    setError('')
    try {
      const saved = await saveQuickReply(accountKey, editingIndex, {
        label,
        content: draftContent,
      })
      setReplies(saved)
      setView('tools')
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'The quick button could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  const openCalendar = async () => {
    setView('calendar')
    setError('')
    setCalendarLoading(true)
    try {
      const loadedServices = await getServices()
      const availableServices = loadedServices.filter(service => !service.lineKey || service.lineKey === accountKey)
      setServices(loadedServices)
      setSelectedServiceId(current => (
        availableServices.some(service => service.id === current) ? current : availableServices[0]?.id ?? ''
      ))
      if (availableServices.length === 0) setError('No services are configured for this SMS line.')
    } catch {
      setError('Services could not be loaded for the availability view.')
    } finally {
      setCalendarLoading(false)
    }
  }

  const title = view === 'tools' ? 'Quick tools' : view === 'edit' ? 'Edit quick button' : 'Live availability'

  return (
    <div className="fixed inset-0 z-[75] flex items-end justify-center" data-testid="quick-tools-sheet">
      <button
        type="button"
        aria-label="Close quick tools"
        onClick={onClose}
        className="absolute inset-0 bg-slate-950/45 backdrop-blur-[2px]"
      />
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="quick-tools-title"
        className="quick-tools-sheet relative flex max-h-[82dvh] w-full max-w-2xl flex-col overflow-hidden rounded-t-[28px] border border-slate-200 bg-white shadow-[0_-20px_60px_rgba(15,23,42,0.28)]"
      >
        <div className="mx-auto mt-2 h-1 w-11 shrink-0 rounded-full bg-slate-300" />
        <header className="flex shrink-0 items-center gap-3 border-b border-slate-100 px-4 pb-3 pt-2">
          {view !== 'tools' && (
            <button
              type="button"
              onClick={() => { setView('tools'); setError('') }}
              className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-700"
              aria-label="Back to quick tools"
            >
              <ChevronLeft className="h-5 w-5" />
            </button>
          )}
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-black uppercase tracking-[0.18em] text-indigo-600">
              {accountKey === 'secondary' ? 'SMS line 2' : 'SMS line 1'}
            </p>
            <h2 id="quick-tools-title" className="truncate text-lg font-black tracking-tight text-slate-900">{title}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-600"
            aria-label="Close quick tools"
          >
            <X className="h-5 w-5" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-[calc(1rem+env(safe-area-inset-bottom))] pt-4">
          {error && <p className="mb-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold text-rose-700">{error}</p>}

          {view === 'tools' && (
            <div className="space-y-4">
              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <p className="text-xs font-black uppercase tracking-wider text-slate-500">Saved text</p>
                  <p className="text-[10px] font-semibold text-slate-400">Tap to paste · hold to edit</p>
                </div>
                <div className="grid grid-cols-5 gap-1.5">
                  {replies.map((reply, index) => (
                    <button
                      key={index}
                      type="button"
                      onPointerDown={event => startLongPress(event, index)}
                      onPointerUp={endLongPress}
                      onPointerCancel={endLongPress}
                      onPointerLeave={endLongPress}
                      onContextMenu={event => { event.preventDefault(); longPressedIndexRef.current = index; editReply(index) }}
                      onClick={() => useReply(index)}
                      disabled={repliesLoading}
                      aria-label={`${reply.label || `Button ${index + 1}`}. Tap to paste. Hold to edit.`}
                      className="min-h-9 min-w-0 select-none rounded-lg border border-indigo-100 bg-indigo-50 px-1.5 py-2 text-center shadow-sm transition active:scale-[0.96] disabled:opacity-50"
                      style={{ touchAction: 'manipulation' }}
                    >
                      <span className="block truncate text-[10px] font-black leading-4 text-indigo-800">{reply.label}</span>
                    </button>
                  ))}
                </div>
              </div>

              <button
                type="button"
                onClick={openCalendar}
                className="flex min-h-16 w-full items-center gap-3 rounded-2xl border border-emerald-200 bg-gradient-to-r from-emerald-600 to-teal-600 px-4 text-left text-white shadow-lg shadow-emerald-900/15 active:scale-[0.99]"
              >
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-white/15"><CalendarDays className="h-5 w-5" /></span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-black">Check calendar</span>
                  <span className="block text-[10px] font-semibold text-emerald-100">See live availability without leaving this chat</span>
                </span>
                <ChevronLeft className="h-5 w-5 rotate-180" />
              </button>

              <p className="text-center text-[10px] font-medium text-slate-400">More conversation tools can be added here without crowding the message screen.</p>
            </div>
          )}

          {view === 'edit' && (
            <div className="space-y-4">
              <div className="rounded-2xl border border-indigo-100 bg-indigo-50 p-3">
                <div className="flex items-center gap-2 text-indigo-800"><Sparkles className="h-4 w-4" /><p className="text-xs font-black">Button {editingIndex + 1}</p></div>
                <p className="mt-1 text-[10px] leading-4 text-indigo-600">Give it a short label, then save the exact text you want inserted into the message box.</p>
              </div>
              <label className="block text-xs font-black text-slate-700">
                Button label <span className="font-medium text-slate-400">({draftLabel.length}/8)</span>
                <input
                  value={draftLabel}
                  onChange={event => setDraftLabel(event.target.value.slice(0, 8))}
                  maxLength={8}
                  autoCapitalize="characters"
                  placeholder="ADDR"
                  className="mt-1.5 min-h-12 w-full rounded-xl border border-slate-300 bg-slate-50 px-3 text-base font-black tracking-wide outline-none focus:border-indigo-500 focus:bg-white"
                />
              </label>
              <label className="block text-xs font-black text-slate-700">
                Text to insert
                <textarea
                  value={draftContent}
                  onChange={event => setDraftContent(event.target.value)}
                  rows={7}
                  maxLength={4000}
                  placeholder="Type an address, link or reusable message…"
                  className="mt-1.5 w-full resize-none rounded-2xl border border-slate-300 bg-slate-50 px-3 py-3 text-[15px] leading-5 outline-none focus:border-indigo-500 focus:bg-white"
                />
              </label>
              <button
                type="button"
                onClick={persistReply}
                disabled={!draftLabel.trim() || saving}
                className="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 text-sm font-black text-white shadow-lg shadow-indigo-900/15 disabled:bg-slate-300"
              >
                <Save className="h-4 w-4" /> {saving ? 'Saving…' : 'Save button'}
              </button>
            </div>
          )}

          {view === 'calendar' && (
            <div className="space-y-4">
              <div className="flex gap-2 overflow-x-auto pb-1">
                {lineServices.map(service => (
                  <button
                    key={service.id}
                    type="button"
                    onClick={() => setSelectedServiceId(service.id)}
                    className={`min-h-10 shrink-0 rounded-full border px-4 text-xs font-black transition ${selectedServiceId === service.id ? 'border-emerald-600 bg-emerald-600 text-white' : 'border-slate-300 bg-white text-slate-700'}`}
                  >
                    {service.name}{service.showDuration === false ? '' : ` · ${service.duration}m`}
                  </button>
                ))}
              </div>

              <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3">
                <div className="flex items-center gap-2">
                  <span className="relative flex h-2.5 w-2.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
                    <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" />
                  </span>
                  <p className="text-[10px] font-black uppercase tracking-wider text-slate-600">Live calendar · Australia/Melbourne</p>
                </div>
                {selectedService && <p className="mt-1 text-xs font-semibold text-slate-500">Openings long enough for {selectedService.name}.</p>}
              </div>

              {calendarLoading ? (
                <div className="grid min-h-48 place-items-center text-center">
                  <div><Clock3 className="mx-auto h-7 w-7 animate-pulse text-emerald-600" /><p className="mt-2 text-xs font-bold text-slate-500">Checking live availability…</p></div>
                </div>
              ) : availabilityGroups.length === 0 && selectedService ? (
                <div className="rounded-2xl border border-dashed border-slate-300 px-4 py-10 text-center">
                  <CalendarDays className="mx-auto h-7 w-7 text-slate-300" />
                  <p className="mt-2 text-sm font-black text-slate-700">No openings found</p>
                  <p className="mt-1 text-[11px] text-slate-500">Try another service duration or check again shortly.</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {availabilityGroups.map(group => (
                    <article key={group.key} className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
                      <h3 className="text-xs font-black uppercase tracking-wider text-slate-700">{group.label}</h3>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {group.slots.map(slot => (
                          <span key={slot.id} className="rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-1.5 text-xs font-black text-emerald-800">{slot.label}</span>
                        ))}
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
