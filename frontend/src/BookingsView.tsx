import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  listBookings,
  updateBooking,
  deleteBooking,
  listThreads,
  getWorkingHours,
  createArrivalInvite,
  CalendarBooking,
  ThreadListItem,
  WorkingHourEntry
} from './api';
import {
  Calendar,
  Trash2,
  RefreshCw,
  Phone,
  Clock,
  ChevronLeft,
  ChevronRight,
  CalendarDays,
  Edit3,
  CheckCircle2,
  UserX,
  List,
  Grid,
  FileText,
  X,
  AlertCircle,
  GripHorizontal,
  ChevronDown,
  ChevronUp,
  Lock,
  Link2,
  Copy
} from 'lucide-react';

interface BookingsViewProps {
  onOpenThread: (threadId: string) => void;
}

const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

const HOUR_HEIGHT_PX = 80;
const PX_PER_MIN = HOUR_HEIGHT_PX / 60;
const TIMELINE_START_H = 7;
const TIMELINE_END_H = 21;
const TOTAL_HOURS = TIMELINE_END_H - TIMELINE_START_H;
const SNAP_MINUTES = 15;

function canonicalPhone(phone: string) {
  let digits = phone.replace(/\D/g, '');
  if (digits.startsWith('00')) digits = digits.slice(2);
  if (digits.startsWith('610')) {
    digits = `61${digits.slice(3)}`;
  } else if (digits.startsWith('0') && digits.length === 10) {
    digits = `61${digits.slice(1)}`;
  } else if (digits.length === 9 && digits.startsWith('4')) {
    digits = `61${digits}`;
  }
  return digits;
}

function selectSmsAccount(
  booking: CalendarBooking,
  phoneMatches: ThreadListItem[],
): 'primary' | 'secondary' | null {
  if (booking.smsAccountKey) return booking.smsAccountKey;
  const uniqueAccounts = [...new Set(phoneMatches.map(thread => thread.smsAccountKey))];
  if (uniqueAccounts.length === 1) return uniqueAccounts[0];
  const answer = window.prompt(
    'Choose the SMS line for this older booking: enter 1 for Tori or 2 for Anonymous.',
  )?.trim();
  if (answer === '1') return 'primary';
  if (answer === '2') return 'secondary';
  return null;
}

function formatLocalDateISO(date: Date): string {
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function minsFromTimelineStart(date: Date): number {
  return date.getHours() * 60 + date.getMinutes() - TIMELINE_START_H * 60;
}

function snapToGrid(mins: number): number {
  return Math.round(mins / SNAP_MINUTES) * SNAP_MINUTES;
}

function minutesToPx(mins: number): number {
  return mins * PX_PER_MIN;
}

function pxToMins(px: number): number {
  return px / PX_PER_MIN;
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: true });
}

type StatusType = 'scheduled' | 'completed' | 'no_show' | 'cancelled';

function StatusBadge({ status }: { status?: string }) {
  const st = status || 'scheduled';
  switch (st) {
    case 'completed':
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200 whitespace-nowrap">
          <CheckCircle2 className="w-2.5 h-2.5" /> Done
        </span>
      );
    case 'no_show':
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-amber-50 text-amber-700 border border-amber-200 whitespace-nowrap">
          <UserX className="w-2.5 h-2.5" /> No-Show
        </span>
      );
    case 'cancelled':
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-rose-50 text-rose-700 border border-rose-200 whitespace-nowrap">
          <X className="w-2.5 h-2.5" /> Cancelled
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-indigo-50 text-indigo-700 border border-indigo-200 whitespace-nowrap">
          <Clock className="w-2.5 h-2.5" /> Scheduled
        </span>
      );
  }
}

interface BookingCardProps {
  booking: CalendarBooking;
  topPx: number;
  heightPx: number;
  isExpanded: boolean;
  isDragging: boolean;
  onToggleExpand: () => void;
  onDragStart: (e: React.MouseEvent, bookingId: string) => void;
  onResizeStart: (e: React.MouseEvent, bookingId: string) => void;
  onEdit: (booking: CalendarBooking) => void;
  onDelete: (id: string) => void;
  onStatusChange: (booking: CalendarBooking, status: StatusType) => void;
  onOpenThread: (booking: CalendarBooking) => void;
  onCreateArrivalLink: (booking: CalendarBooking) => void;
  openingBookingId: string | null;
}

function BookingCard({
  booking, topPx, heightPx, isExpanded, isDragging,
  onToggleExpand, onDragStart, onResizeStart,
  onEdit, onDelete, onStatusChange, onOpenThread, onCreateArrivalLink
}: BookingCardProps) {
  const sDate = new Date(booking.startTime);
  const eDate = new Date(booking.endTime);
  const durationMins = Math.round((eDate.getTime() - sDate.getTime()) / 60000);
  const isVeryShort = heightPx < 32;
  const isShort = heightPx < 56;

  const statusColor: Record<string, string> = {
    completed: 'border-l-emerald-500 bg-emerald-50/90',
    no_show: 'border-l-amber-400 bg-amber-50/90',
    cancelled: 'border-l-rose-400 bg-rose-50/90',
    scheduled: 'border-l-indigo-500 bg-white',
  };
  const colorClass = statusColor[booking.status || 'scheduled'] || statusColor.scheduled;

  return (
    <div
      style={{ top: topPx, height: Math.max(heightPx, 18), left: 4, right: 4 }}
      className={`absolute z-10 rounded-lg border border-slate-200 border-l-[3px] shadow-sm select-none overflow-hidden ${colorClass} ${isDragging ? 'opacity-40 ring-2 ring-rose-400 z-30' : 'hover:shadow-md hover:z-20'}`}
    >
      {/* Draggable overlay — covers main card area, excludes bottom resize strip */}
      <div
        className="absolute inset-x-0 top-0 cursor-grab active:cursor-grabbing z-10"
        style={{ height: isVeryShort ? '100%' : 'calc(100% - 12px)' }}
        onMouseDown={e => onDragStart(e, booking.id)}
      />

      {/* Content */}
      <div className="relative z-10 h-full flex flex-col px-1.5 pt-1 pb-3 pointer-events-none" style={{ gap: isVeryShort ? 0 : 1 }}>
        {/* Title row — always shown */}
        <div className="flex items-center gap-1 min-w-0 pr-4">
          <span className={`font-bold text-slate-800 truncate leading-tight ${isVeryShort ? 'text-[9px]' : 'text-[10px]'}`}>
            {booking.summary}
          </span>
          {!isVeryShort && <StatusBadge status={booking.status} />}
        </div>

        {/* Time — shown when not tiny */}
        {!isVeryShort && (
          <span className="text-[9px] text-slate-500 font-semibold leading-none truncate">
            {formatTime(sDate)}–{formatTime(eDate)} · {durationMins}m
          </span>
        )}

        {/* Expanded section */}
        {isExpanded && !isShort && (
          <div className="flex flex-col gap-0.5 mt-0.5 pointer-events-auto">
            {booking.customerPhone && (
              <div className="flex items-center gap-1">
                <button
                  onMouseDown={e => e.stopPropagation()}
                  onClick={e => { e.stopPropagation(); onCreateArrivalLink(booking); }}
                  className="px-1.5 py-0.5 text-[8px] font-bold rounded bg-indigo-50 text-indigo-700 border border-indigo-200 cursor-pointer"
                >Arrival link</button>
                <button
                  onMouseDown={e => e.stopPropagation()}
                  onClick={e => { e.stopPropagation(); onOpenThread(booking); }}
                  className="flex items-center gap-1 text-[9px] text-indigo-600 font-bold hover:underline cursor-pointer w-fit"
                >
                  <Phone className="w-2.5 h-2.5" /> {booking.customerPhone}
                </button>
              </div>
            )}
            {booking.notes && (
              <p className="text-[9px] text-slate-400 italic leading-tight line-clamp-2">{booking.notes}</p>
            )}
            <div className="flex items-center gap-1 mt-0.5 flex-wrap">
              <button
                onMouseDown={e => e.stopPropagation()}
                onClick={e => { e.stopPropagation(); onStatusChange(booking, 'completed'); }}
                className={`px-1.5 py-0.5 text-[8px] font-bold rounded cursor-pointer ${booking.status === 'completed' ? 'bg-emerald-600 text-white' : 'bg-emerald-50 text-emerald-700 border border-emerald-200'}`}
              >Done</button>
              <button
                onMouseDown={e => e.stopPropagation()}
                onClick={e => { e.stopPropagation(); onStatusChange(booking, 'no_show'); }}
                className={`px-1.5 py-0.5 text-[8px] font-bold rounded cursor-pointer ${booking.status === 'no_show' ? 'bg-amber-600 text-white' : 'bg-amber-50 text-amber-700 border border-amber-200'}`}
              >No-Show</button>
              <button
                onMouseDown={e => e.stopPropagation()}
                onClick={e => { e.stopPropagation(); onEdit(booking); }}
                className="px-1.5 py-0.5 text-[8px] font-bold rounded bg-slate-100 text-slate-700 border border-slate-200 cursor-pointer"
              >Edit</button>
              <button
                onMouseDown={e => e.stopPropagation()}
                onClick={e => { e.stopPropagation(); onDelete(booking.id); }}
                className="px-1.5 py-0.5 text-[8px] font-bold rounded bg-rose-50 text-rose-700 border border-rose-200 cursor-pointer"
              >Delete</button>
            </div>
          </div>
        )}
      </div>

      {/* Expand/collapse button */}
      {!isVeryShort && (
        <button
          onMouseDown={e => e.stopPropagation()}
          onClick={e => { e.stopPropagation(); onToggleExpand(); }}
          className="absolute top-0.5 right-0.5 z-20 p-0.5 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100/80 cursor-pointer pointer-events-auto"
          title={isExpanded ? 'Collapse' : 'See details'}
        >
          {isExpanded ? <ChevronUp className="w-2.5 h-2.5" /> : <ChevronDown className="w-2.5 h-2.5" />}
        </button>
      )}

      {/* Resize handle */}
      <div
        className="absolute inset-x-0 bottom-0 h-3 flex items-center justify-center cursor-ns-resize z-20 pointer-events-auto group"
        onMouseDown={e => { e.stopPropagation(); onResizeStart(e, booking.id); }}
        title="Drag to change duration"
      >
        <GripHorizontal className="w-3 h-3 text-slate-300 group-hover:text-slate-500 transition-colors" />
      </div>
    </div>
  );
}

export default function BookingsView({ onOpenThread }: BookingsViewProps) {
  const [bookings, setBookings] = useState<CalendarBooking[]>([]);
  const [workingHours, setWorkingHours] = useState<WorkingHourEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'day' | 'list'>('day');
  const [selectedDate, setSelectedDate] = useState<Date>(new Date());
  const [dateInputVal, setDateInputVal] = useState<string>(formatLocalDateISO(new Date()));
  const [now, setNow] = useState<Date>(new Date());
  const [editingBooking, setEditingBooking] = useState<CalendarBooking | null>(null);
  const [deleteConfirmBookingId, setDeleteConfirmBookingId] = useState<string | null>(null);
  const [openingBookingId, setOpeningBookingId] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [arrivalLink, setArrivalLink] = useState<{ booking: CalendarBooking; link: string } | null>(null);
  const [generatingArrivalId, setGeneratingArrivalId] = useState<string | null>(null);

  const [editForm, setEditForm] = useState({
    summary: '',
    customerPhone: '',
    dateStr: '',
    startTimeStr: '09:00',
    endTimeStr: '09:30',
    status: 'scheduled' as StatusType,
    notes: ''
  });

  const dragRef = useRef<{
    type: 'move' | 'resize';
    bookingId: string;
    startMouseY: number;
    origStartMins: number;
    origEndMins: number;
  } | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dragPreview, setDragPreview] = useState<{ startMins: number; endMins: number } | null>(null);

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(timer);
  }, []);

  const fetchAllData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [bookingsData, whData] = await Promise.all([
        listBookings(),
        getWorkingHours().catch(() => [])
      ]);
      bookingsData.sort((a, b) => new Date(a.startTime).getTime() - new Date(b.startTime).getTime());
      setBookings(bookingsData);
      if (whData && whData.length > 0) setWorkingHours(whData);
    } catch {
      setError('Failed to load appointments. Please check backend connection.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAllData(); }, [fetchAllData]);

  useEffect(() => { setDateInputVal(formatLocalDateISO(selectedDate)); }, [selectedDate]);

  useEffect(() => {
    if (successMsg) {
      const t = setTimeout(() => setSuccessMsg(null), 4000);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [successMsg]);

  const handlePrevDay = () => setSelectedDate(prev => {
    const d = new Date(prev); d.setDate(d.getDate() - 1); return d;
  });
  const handleNextDay = () => setSelectedDate(prev => {
    const d = new Date(prev); d.setDate(d.getDate() + 1); return d;
  });
  const handleToday = () => setSelectedDate(new Date());

  const handleDateInputChange = (val: string) => {
    setDateInputVal(val);
    if (val && !isNaN(Date.parse(val))) {
      const [y, m, d] = val.split('-').map(Number);
      if (!isNaN(y) && !isNaN(m) && !isNaN(d)) setSelectedDate(new Date(y, m - 1, d));
    }
  };

  const currentDayWorkingHours = useMemo(() => {
    const dayName = DAY_NAMES[selectedDate.getDay()];
    const entry = workingHours.find(wh => wh.day.toLowerCase() === dayName.toLowerCase());
    if (!entry) {
      const isWeekend = selectedDate.getDay() === 0 || selectedDate.getDay() === 6;
      return { enabled: !isWeekend, open: '09:00', close: '17:00', openMins: 9 * 60, closeMins: 17 * 60 };
    }
    const [openH, openM] = entry.open.split(':').map(Number);
    const [closeH, closeM] = entry.close.split(':').map(Number);
    return {
      enabled: entry.enabled,
      open: entry.open,
      close: entry.close,
      openMins: (openH || 0) * 60 + (openM || 0),
      closeMins: (closeH || 0) * 60 + (closeM || 0)
    };
  }, [selectedDate, workingHours]);

  const handleUpdateStatus = async (booking: CalendarBooking, newStatus: StatusType) => {
    try {
      const updated = await updateBooking(booking.id, { status: newStatus });
      setBookings(prev => prev.map(b => b.id === booking.id ? { ...b, status: newStatus, ...updated } : b));
      setSuccessMsg(`Marked as ${newStatus.replace('_', ' ')}.`);
    } catch {
      setError('Failed to update status.');
    }
  };

  const confirmDelete = async (id: string) => {
    try {
      await deleteBooking(id);
      setBookings(prev => prev.filter(b => b.id !== id));
      setSuccessMsg('Booking cancelled.');
    } catch {
      setError('Failed to delete booking.');
    }
  };

  const startEditing = (booking: CalendarBooking) => {
    const sDate = new Date(booking.startTime);
    const eDate = new Date(booking.endTime);
    setEditForm({
      summary: booking.summary || '',
      customerPhone: booking.customerPhone || '',
      dateStr: formatLocalDateISO(sDate),
      startTimeStr: `${String(sDate.getHours()).padStart(2, '0')}:${String(sDate.getMinutes()).padStart(2, '0')}`,
      endTimeStr: `${String(eDate.getHours()).padStart(2, '0')}:${String(eDate.getMinutes()).padStart(2, '0')}`,
      status: (booking.status as StatusType) || 'scheduled',
      notes: booking.notes || ''
    });
    setEditingBooking(booking);
  };

  const handleSaveEdit = async () => {
    if (!editingBooking) return;
    try {
      const [startH, startM] = editForm.startTimeStr.split(':').map(Number);
      const [endH, endM] = editForm.endTimeStr.split(':').map(Number);
      const [y, m, d] = editForm.dateStr.split('-').map(Number);
      const newStart = new Date(y, m - 1, d, startH, startM);
      const newEnd = new Date(y, m - 1, d, endH, endM);
      const payload = {
        summary: editForm.summary,
        customerPhone: editForm.customerPhone,
        startTime: newStart.toISOString(),
        endTime: newEnd.toISOString(),
        status: editForm.status,
        notes: editForm.notes
      };
      const updated = await updateBooking(editingBooking.id, payload);
      setBookings(prev => prev.map(b => b.id === editingBooking.id ? { ...b, ...payload, ...updated } : b));
      setEditingBooking(null);
      setSuccessMsg('Appointment updated.');
    } catch {
      setError('Failed to save changes.');
    }
  };

  const openCustomerThread = async (booking: CalendarBooking) => {
    if (!booking.customerPhone || openingBookingId) return;
    setOpeningBookingId(booking.id);
    try {
      const targetPhone = canonicalPhone(booking.customerPhone);
      const threads = await listThreads();
      const phoneMatches = threads.filter(t => canonicalPhone(t.customerPhone) === targetPhone);
      const storedThread = phoneMatches.find(thread => (
        thread.id === booking.threadId
        && (!booking.smsAccountKey || thread.smsAccountKey === booking.smsAccountKey)
      ));
      if (storedThread) {
        onOpenThread(storedThread.id);
        return;
      }
      const account = selectSmsAccount(booking, phoneMatches);
      if (!account) {
        setError('Choose Tori or Anonymous to open the correct conversation.');
        return;
      }
      const match = phoneMatches.find(thread => thread.smsAccountKey === account);
      if (!match) {
        setError(`No SMS conversation for ${booking.customerPhone}.`);
        return;
      }
      onOpenThread(match.id);
    } catch {
      setError('Could not open conversation.');
    } finally {
      setOpeningBookingId(null);
    }
  };

  const generateArrivalLink = async (booking: CalendarBooking) => {
    if (generatingArrivalId) return;
    setGeneratingArrivalId(booking.id);
    setError(null);
    try {
      const targetPhone = canonicalPhone(booking.customerPhone || '');
      const threads = await listThreads();
      const phoneMatches = threads.filter(thread => canonicalPhone(thread.customerPhone) === targetPhone);
      const storedThread = phoneMatches.find(thread => (
        thread.id === booking.threadId
        && (!booking.smsAccountKey || thread.smsAccountKey === booking.smsAccountKey)
      ));
      const account = storedThread?.smsAccountKey || selectSmsAccount(booking, phoneMatches);
      if (!account) throw new Error('Choose Tori or Anonymous to create the link on the correct SMS line.');
      const thread = storedThread || phoneMatches.find(item => item.smsAccountKey === account);
      const result = await createArrivalInvite(booking, account, thread?.id);
      const boundBooking = {
        ...booking,
        smsAccountKey: result.session.smsAccountKey,
        threadId: result.session.threadId,
      };
      setBookings(current => current.map(item => item.id === booking.id ? boundBooking : item));
      setArrivalLink({ booking: boundBooking, link: result.link });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the arrival link.');
    } finally {
      setGeneratingArrivalId(null);
    }
  };

  const copyArrivalLink = async () => {
    if (!arrivalLink) return;
    await navigator.clipboard.writeText(arrivalLink.link);
    setSuccessMsg('Arrival link copied. The previous link for this booking is now disabled.');
  };

  const dayFilteredBookings = useMemo(() => {
    const selY = selectedDate.getFullYear();
    const selM = selectedDate.getMonth();
    const selD = selectedDate.getDate();
    return bookings.filter(b => {
      const d = new Date(b.startTime);
      return d.getFullYear() === selY && d.getMonth() === selM && d.getDate() === selD;
    });
  }, [bookings, selectedDate]);

  const isSelectedDateToday = useMemo(() =>
    formatLocalDateISO(selectedDate) === formatLocalDateISO(now),
    [selectedDate, now]
  );

  const nowTopPx = useMemo(() => {
    if (!isSelectedDateToday) return null;
    const totalMins = now.getHours() * 60 + now.getMinutes();
    const start = TIMELINE_START_H * 60;
    const end = TIMELINE_END_H * 60;
    if (totalMins < start || totalMins > end) return null;
    return minutesToPx(totalMins - start);
  }, [now, isSelectedDateToday]);

  // ── Mouse drag for move ──────────────────────────────────────────────────
  const handleDragStart = (e: React.MouseEvent, bookingId: string) => {
    e.preventDefault();
    const booking = bookings.find(b => b.id === bookingId);
    if (!booking) return;
    const s = new Date(booking.startTime);
    const en = new Date(booking.endTime);
    dragRef.current = {
      type: 'move',
      bookingId,
      startMouseY: e.clientY,
      origStartMins: s.getHours() * 60 + s.getMinutes(),
      origEndMins: en.getHours() * 60 + en.getMinutes()
    };
    setDraggingId(bookingId);
    setDragPreview({ startMins: s.getHours() * 60 + s.getMinutes(), endMins: en.getHours() * 60 + en.getMinutes() });
  };

  // ── Mouse drag for resize ─────────────────────────────────────────────────
  const handleResizeStart = (e: React.MouseEvent, bookingId: string) => {
    e.preventDefault();
    e.stopPropagation();
    const booking = bookings.find(b => b.id === bookingId);
    if (!booking) return;
    const s = new Date(booking.startTime);
    const en = new Date(booking.endTime);
    dragRef.current = {
      type: 'resize',
      bookingId,
      startMouseY: e.clientY,
      origStartMins: s.getHours() * 60 + s.getMinutes(),
      origEndMins: en.getHours() * 60 + en.getMinutes()
    };
    setDraggingId(bookingId);
    setDragPreview({ startMins: s.getHours() * 60 + s.getMinutes(), endMins: en.getHours() * 60 + en.getMinutes() });
  };

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!dragRef.current) return;
      const { type, startMouseY, origStartMins, origEndMins } = dragRef.current;
      const deltaMins = pxToMins(e.clientY - startMouseY);
      const duration = origEndMins - origStartMins;

      if (type === 'move') {
        let newStart = snapToGrid(origStartMins + deltaMins);
        newStart = Math.max(TIMELINE_START_H * 60, Math.min(TIMELINE_END_H * 60 - 15, newStart));
        setDragPreview({ startMins: newStart, endMins: newStart + duration });
      } else {
        let newEnd = snapToGrid(origEndMins + deltaMins);
        newEnd = Math.max(origStartMins + 15, Math.min(TIMELINE_END_H * 60, newEnd));
        setDragPreview({ startMins: origStartMins, endMins: newEnd });
      }
    };

    const onMouseUp = async () => {
      if (!dragRef.current || !dragPreview) {
        dragRef.current = null;
        setDraggingId(null);
        setDragPreview(null);
        return;
      }
      const { bookingId } = dragRef.current;
      const booking = bookings.find(b => b.id === bookingId);
      if (booking) {
        const newStart = new Date(selectedDate);
        newStart.setHours(Math.floor(dragPreview.startMins / 60), dragPreview.startMins % 60, 0, 0);
        const newEnd = new Date(selectedDate);
        newEnd.setHours(Math.floor(dragPreview.endMins / 60), dragPreview.endMins % 60, 0, 0);
        const payload = { startTime: newStart.toISOString(), endTime: newEnd.toISOString() };
        try {
          const updated = await updateBooking(bookingId, payload);
          setBookings(prev => prev.map(b => b.id === bookingId ? { ...b, ...payload, ...updated } : b));
          setSuccessMsg(`Updated "${booking.summary}" ${formatTime(newStart)}–${formatTime(newEnd)}`);
        } catch {
          setError('Failed to reschedule.');
        }
      }
      dragRef.current = null;
      setDraggingId(null);
      setDragPreview(null);
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, [bookings, dragPreview, selectedDate]);

  const toggleExpand = (id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const timelineHours = useMemo(() => {
    const h: number[] = [];
    for (let i = TIMELINE_START_H; i <= TIMELINE_END_H; i++) h.push(i);
    return h;
  }, []);

  const totalGridHeight = TOTAL_HOURS * HOUR_HEIGHT_PX;

  return (
    <div className="flex-1 bg-[#faf9f6] flex flex-col h-full overflow-hidden p-3 sm:p-5 font-sans text-slate-800">

      {/* ── Header ── */}
      <div className="bg-white rounded-2xl border border-slate-150 p-4 sm:p-5 shadow-xs mb-4 shrink-0 flex flex-col lg:flex-row justify-between items-stretch lg:items-center gap-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-rose-50 border border-rose-100 flex items-center justify-center text-rose-600 shadow-2xs shrink-0">
            <CalendarDays className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-base sm:text-lg font-bold text-slate-800 flex items-center gap-2">
              Appointments &amp; Bookings
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 font-bold text-slate-600 border border-slate-200">
                {bookings.length} Total
              </span>
            </h1>
            <p className="text-[11px] text-slate-500 font-semibold mt-0.5">
              <span className="text-rose-600 font-bold">{DAY_NAMES[selectedDate.getDay()]}</span>
              {' · '}
              {selectedDate.toLocaleDateString('en-AU', { day: 'numeric', month: 'long', year: 'numeric' })}
            </p>
          </div>
        </div>

        {/* Day navigation */}
        <div className="flex items-center gap-2 bg-slate-50 p-1.5 rounded-xl border border-slate-200 shadow-2xs justify-center flex-wrap sm:flex-nowrap">
          <button
            onClick={handlePrevDay}
            className="p-1.5 hover:bg-white rounded-lg border border-transparent hover:border-slate-200 transition-all text-slate-600 cursor-pointer"
            title="Previous Day"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <button
            onClick={handleToday}
            className="px-2.5 py-1 text-xs font-bold bg-white border border-slate-200 rounded-lg text-slate-700 hover:bg-slate-100 transition-all cursor-pointer shadow-2xs"
          >
            Today
          </button>
          <button
            onClick={handleNextDay}
            className="p-1.5 hover:bg-white rounded-lg border border-transparent hover:border-slate-200 transition-all text-slate-600 cursor-pointer"
            title="Next Day"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
          <div className="h-4 w-px bg-slate-200 mx-1 hidden sm:block" />
          <div className="flex items-center gap-1 bg-white px-2.5 py-1 rounded-lg border border-slate-200">
            <Calendar className="w-3.5 h-3.5 text-rose-500 shrink-0" />
            <input
              type="date"
              value={dateInputVal}
              onChange={e => handleDateInputChange(e.target.value)}
              className="text-xs font-bold text-slate-700 focus:outline-none bg-transparent cursor-pointer"
            />
          </div>
        </div>

        {/* Right controls */}
        <div className="flex items-center justify-between sm:justify-end gap-2.5 flex-wrap">
          {/* Working Hours Badge */}
          {currentDayWorkingHours.enabled ? (
            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-emerald-50 border border-emerald-200 text-emerald-700 font-extrabold text-[11px] shrink-0">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              {currentDayWorkingHours.open} – {currentDayWorkingHours.close}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-slate-100 border border-slate-200 text-slate-600 font-bold text-[11px] shrink-0">
              <Lock className="w-3 h-3 text-slate-400" /> Closed
            </span>
          )}
          <div className="flex items-center bg-slate-100 p-1 rounded-xl border border-slate-200">
            <button
              onClick={() => setViewMode('day')}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg transition-all cursor-pointer ${viewMode === 'day' ? 'bg-white text-rose-600 shadow-2xs border border-slate-200' : 'text-slate-500 hover:text-slate-800'}`}
            >
              <Grid className="w-3.5 h-3.5" /> Day
            </button>
            <button
              onClick={() => setViewMode('list')}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg transition-all cursor-pointer ${viewMode === 'list' ? 'bg-white text-rose-600 shadow-2xs border border-slate-200' : 'text-slate-500 hover:text-slate-800'}`}
            >
              <List className="w-3.5 h-3.5" /> List
            </button>
          </div>
          <button
            onClick={fetchAllData}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-2 text-xs font-bold bg-white hover:bg-slate-50 border border-slate-200 rounded-xl transition-all cursor-pointer text-slate-700 shadow-2xs"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            <span className="hidden sm:inline">Refresh</span>
          </button>
        </div>
      </div>

      {/* ── Alerts ── */}
      {error && (
        <div className="mb-4 p-3 bg-rose-50 border border-rose-200 text-rose-700 text-xs rounded-xl font-bold flex items-center justify-between shadow-2xs shrink-0">
          <div className="flex items-center gap-2"><AlertCircle className="w-4 h-4 shrink-0" /><span>{error}</span></div>
          <button onClick={() => setError(null)} className="text-rose-400 hover:text-rose-600 cursor-pointer"><X className="w-4 h-4" /></button>
        </div>
      )}
      {successMsg && (
        <div className="mb-4 p-3 bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs rounded-xl font-bold flex items-center justify-between shadow-2xs shrink-0">
          <div className="flex items-center gap-2"><CheckCircle2 className="w-4 h-4 shrink-0" /><span>{successMsg}</span></div>
          <button onClick={() => setSuccessMsg(null)} className="text-emerald-400 hover:text-emerald-600 cursor-pointer"><X className="w-4 h-4" /></button>
        </div>
      )}



      {/* ── Main View Area ── */}
      <div className="flex-1 bg-white rounded-2xl border border-slate-150 shadow-xs overflow-hidden flex flex-col min-h-0">

        {loading && bookings.length === 0 ? (
          <div className="py-24 text-center text-xs text-slate-400 font-bold">
            <div className="w-8 h-8 border-2 border-rose-200 border-t-rose-600 rounded-full animate-spin mx-auto mb-3" />
            Loading schedule...
          </div>

        ) : viewMode === 'day' ? (

          /* ──────────────────────────────────────────────────────────────────
             DAY VIEW — Absolute-positioned timeline. Cards take up exactly
             the height matching their duration. The container scrolls.
          ─────────────────────────────────────────────────────────────────── */
          <div className="flex-1 overflow-y-auto">
            {/* Single-column grid — time labels are overlaid on the hour lines */}
            <div className="relative" style={{ height: totalGridHeight }}>

              {/* Hour rows with inline time label + 15-min dotted sub-lines */}
              {timelineHours.slice(0, -1).map(h => {
                const topPx = (h - TIMELINE_START_H) * HOUR_HEIGHT_PX;
                const hMins = h * 60;
                const inWork = currentDayWorkingHours.enabled
                  && hMins >= currentDayWorkingHours.openMins
                  && hMins < currentDayWorkingHours.closeMins;
                const label = h < 12 ? `${h}am` : h === 12 ? '12pm' : `${h - 12}pm`;
                return (
                  <div
                    key={h}
                    className={`absolute inset-x-0 border-t border-slate-200 ${inWork ? 'bg-white' : 'bg-slate-50/50'}`}
                    style={{ top: topPx, height: HOUR_HEIGHT_PX }}
                  >
                    {/* Time label sits on the separator line */}
                    <span className="absolute left-1.5 -top-[9px] text-[9px] font-bold text-slate-400 bg-inherit leading-none px-0.5 z-10 pointer-events-none">
                      {label}
                    </span>
                    {[15, 30, 45].map(m => (
                      <div
                        key={m}
                        className="absolute inset-x-0 border-t border-dashed border-slate-200/60"
                        style={{ top: minutesToPx(m) }}
                      />
                    ))}
                  </div>
                );
              })}

              {/* Final border */}
              <div className="absolute inset-x-0 border-t border-slate-200" style={{ top: totalGridHeight }} />

              {/* NOW red indicator line */}
              {nowTopPx !== null && (
                <div
                  className="absolute inset-x-0 z-30 flex items-center pointer-events-none"
                  style={{ top: nowTopPx }}
                >
                  <div className="w-2.5 h-2.5 rounded-full bg-rose-500 border-2 border-white shadow-sm ml-1 shrink-0" />
                  <div className="flex-1 h-[2px] bg-rose-500 shadow-sm" />
                  <span className="px-1.5 py-0.5 text-[8px] font-extrabold bg-rose-600 text-white rounded-full mr-1 animate-pulse shrink-0">
                    {formatTime(now)}
                  </span>
                </div>
              )}

              {/* Booking cards */}
              {dayFilteredBookings.map(booking => {
                const sDate = new Date(booking.startTime);
                const eDate = new Date(booking.endTime);

                let startMins = minsFromTimelineStart(sDate);
                let endMins = minsFromTimelineStart(eDate);

                if (draggingId === booking.id && dragPreview) {
                  startMins = dragPreview.startMins - TIMELINE_START_H * 60;
                  endMins = dragPreview.endMins - TIMELINE_START_H * 60;
                }

                const topPx = Math.max(0, minutesToPx(startMins));
                const heightPx = Math.max(minutesToPx(Math.max(endMins - startMins, 0)), 18);

                return (
                  <BookingCard
                    key={booking.id}
                    booking={booking}
                    topPx={topPx}
                    heightPx={heightPx}
                    isExpanded={expandedIds.has(booking.id)}
                    isDragging={draggingId === booking.id}
                    onToggleExpand={() => toggleExpand(booking.id)}
                    onDragStart={handleDragStart}
                    onResizeStart={handleResizeStart}
                    onEdit={startEditing}
                    onDelete={id => setDeleteConfirmBookingId(id)}
                    onStatusChange={handleUpdateStatus}
                    onOpenThread={openCustomerThread}
                    onCreateArrivalLink={generateArrivalLink}
                    openingBookingId={openingBookingId}
                  />
                );
              })}

              {/* Empty state */}
              {dayFilteredBookings.length === 0 && !loading && (
                <div className="absolute inset-0 flex flex-col items-center justify-center text-xs text-slate-400 font-bold gap-2 pointer-events-none">
                  <CalendarDays className="w-10 h-10 text-slate-200 stroke-[1.5]" />
                  <span>No bookings for {DAY_NAMES[selectedDate.getDay()]}</span>
                </div>
              )}
            </div>
          </div>

        ) : (

          /* ── LIST VIEW ── */
          <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
            {dayFilteredBookings.length === 0 ? (
              <div className="py-24 text-center text-xs text-slate-400 font-bold flex flex-col items-center gap-2">
                <Calendar className="w-12 h-12 text-slate-200 stroke-[1.5]" />
                <span>No bookings for {DAY_NAMES[selectedDate.getDay()]}</span>
              </div>
            ) : (
              dayFilteredBookings.map((booking: CalendarBooking) => {
                const dateObj = new Date(booking.startTime);
                const endObj = new Date(booking.endTime);
                return (
                  <div
                    key={booking.id}
                    className="bg-white rounded-xl border border-slate-150 p-4 shadow-2xs hover:shadow-xs transition-all flex flex-col sm:flex-row sm:items-center justify-between gap-4"
                  >
                    <div className="flex flex-col gap-1.5">
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => void openCustomerThread(booking)}
                          disabled={!booking.customerPhone || openingBookingId !== null}
                          className="text-left text-sm font-bold text-indigo-600 hover:underline disabled:text-slate-800 cursor-pointer"
                        >
                          {booking.summary}
                        </button>
                        <StatusBadge status={booking.status} />
                      </div>
                      <div className="flex flex-wrap items-center gap-y-1.5 gap-x-4 text-xs text-slate-500 font-semibold">
                        <span className="flex items-center gap-1.5">
                          <Phone className="w-3.5 h-3.5 text-slate-400" />
                          {booking.customerPhone || 'No Phone'}
                        </span>
                        <span className="flex items-center gap-1.5">
                          <Clock className="w-3.5 h-3.5 text-slate-400" />
                          {dateObj.toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short' })} · {formatTime(dateObj)}–{formatTime(endObj)}
                        </span>
                        {booking.notes && (
                          <span className="flex items-center gap-1 text-slate-400">
                            <FileText className="w-3.5 h-3.5" />{booking.notes}
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0 justify-between sm:justify-end border-t sm:border-t-0 pt-2 sm:pt-0 border-slate-100">
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={() => void generateArrivalLink(booking)}
                          disabled={generatingArrivalId !== null || booking.status === 'cancelled'}
                          className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-bold rounded-xl cursor-pointer bg-indigo-50 text-indigo-700 border border-indigo-200 disabled:opacity-50"
                        ><Link2 className="w-3.5 h-3.5" />{generatingArrivalId === booking.id ? 'Creating…' : 'Arrival link'}</button>
                        <button
                          onClick={() => handleUpdateStatus(booking, 'completed')}
                          className={`px-2.5 py-1 text-xs font-bold rounded-xl cursor-pointer ${booking.status === 'completed' ? 'bg-emerald-600 text-white' : 'bg-emerald-50 text-emerald-700 border border-emerald-200'}`}
                        >Done</button>
                        <button
                          onClick={() => handleUpdateStatus(booking, 'no_show')}
                          className={`px-2.5 py-1 text-xs font-bold rounded-xl cursor-pointer ${booking.status === 'no_show' ? 'bg-amber-600 text-white' : 'bg-amber-50 text-amber-700 border border-amber-200'}`}
                        >No-Show</button>
                      </div>
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => startEditing(booking)}
                          className="px-3 py-1.5 text-xs font-bold text-slate-700 bg-slate-100 hover:bg-slate-200 rounded-xl cursor-pointer"
                        >Edit</button>
                        <button
                          onClick={() => setDeleteConfirmBookingId(booking.id)}
                          className="p-1.5 text-rose-600 hover:bg-rose-50 rounded-xl border border-rose-100 cursor-pointer"
                        ><Trash2 className="w-4 h-4" /></button>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>

      {/* ── Edit Modal ── */}
      {arrivalLink && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4 backdrop-blur-xs">
          <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
                <div><h3 className="text-base font-black text-slate-900">Customer arrival link</h3><p className="mt-1 text-xs text-slate-500">{arrivalLink.booking.summary}</p></div>
              <button onClick={() => setArrivalLink(null)} className="rounded-lg p-1 text-slate-400 hover:bg-slate-100"><X className="h-5 w-5" /></button>
            </div>
              <p className="mt-4 text-sm leading-6 text-slate-600">Send this link to the customer. They can reopen it safely; only the first press of “I’ve arrived” creates the staff alert.</p>
            <div className="mt-4 break-all rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">{arrivalLink.link}</div>
            <button onClick={() => void copyArrivalLink()} className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 py-3 text-sm font-black text-white"><Copy className="h-4 w-4" /> Copy link</button>
            <p className="mt-3 text-center text-[11px] text-slate-400">Creating another link for this booking immediately disables this one.</p>
          </div>
        </div>
      )}

      {editingBooking && (
        <div className="fixed inset-0 bg-slate-950/60 backdrop-blur-xs flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-2xl border border-slate-150 p-6 shadow-2xl max-w-md w-full flex flex-col gap-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="text-base font-bold text-slate-800 flex items-center gap-2">
                <Edit3 className="w-4 h-4 text-rose-500" /> Edit Appointment
              </h3>
              <button onClick={() => setEditingBooking(null)} className="text-slate-400 hover:text-slate-600 p-1 rounded-lg hover:bg-slate-100 cursor-pointer">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex flex-col gap-3 text-xs font-semibold text-slate-700">
              <div>
                <label className="block mb-1 text-slate-500 font-bold">Service / Title</label>
                <input type="text" value={editForm.summary} onChange={e => setEditForm(p => ({ ...p, summary: e.target.value }))} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-1 focus:ring-rose-500 text-slate-800 font-bold" />
              </div>
              <div>
                <label className="block mb-1 text-slate-500 font-bold">Customer Phone</label>
                <input type="text" value={editForm.customerPhone} onChange={e => setEditForm(p => ({ ...p, customerPhone: e.target.value }))} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-1 focus:ring-rose-500 text-slate-800 font-bold" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block mb-1 text-slate-500 font-bold">Date</label>
                  <input type="date" value={editForm.dateStr} onChange={e => setEditForm(p => ({ ...p, dateStr: e.target.value }))} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-1 focus:ring-rose-500 text-slate-800 font-bold" />
                </div>
                <div>
                  <label className="block mb-1 text-slate-500 font-bold">Status</label>
                  <select value={editForm.status} onChange={e => setEditForm(p => ({ ...p, status: e.target.value as StatusType }))} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-1 focus:ring-rose-500 text-slate-800 font-bold">
                    <option value="scheduled">Scheduled 🔵</option>
                    <option value="completed">Completed 🟢</option>
                    <option value="no_show">No-Show 🟠</option>
                    <option value="cancelled">Cancelled 🔴</option>
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block mb-1 text-slate-500 font-bold">Start Time</label>
                  <input type="time" step="900" value={editForm.startTimeStr} onChange={e => setEditForm(p => ({ ...p, startTimeStr: e.target.value }))} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-1 focus:ring-rose-500 text-slate-800 font-bold" />
                </div>
                <div>
                  <label className="block mb-1 text-slate-500 font-bold">End Time</label>
                  <input type="time" step="900" value={editForm.endTimeStr} onChange={e => setEditForm(p => ({ ...p, endTimeStr: e.target.value }))} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-1 focus:ring-rose-500 text-slate-800 font-bold" />
                </div>
              </div>
              <div>
                <label className="block mb-1 text-slate-500 font-bold">Notes</label>
                <textarea rows={3} value={editForm.notes} onChange={e => setEditForm(p => ({ ...p, notes: e.target.value }))} placeholder="Internal notes..." className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-1 focus:ring-rose-500 text-slate-800 font-medium" />
              </div>
            </div>
            <div className="flex justify-end gap-2.5 mt-2 border-t border-slate-100 pt-3">
              <button type="button" onClick={() => setEditingBooking(null)} className="px-4 py-2 rounded-xl border border-slate-200 text-xs font-bold text-slate-600 hover:bg-slate-50 cursor-pointer">Cancel</button>
              <button type="button" onClick={handleSaveEdit} className="px-4 py-2 rounded-xl bg-rose-600 hover:bg-rose-700 text-white text-xs font-bold cursor-pointer shadow-2xs">Save Changes</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Delete Confirm ── */}
      {deleteConfirmBookingId !== null && (
        <div className="fixed inset-0 bg-slate-950/60 backdrop-blur-xs flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-2xl border border-slate-150 p-6 shadow-2xl max-w-sm w-full flex flex-col gap-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-rose-50 flex items-center justify-center text-rose-600 shrink-0">
                <Trash2 className="w-5 h-5" />
              </div>
              <h3 className="text-base font-bold text-slate-800">Cancel Appointment?</h3>
            </div>
            <p className="text-xs text-slate-500 font-semibold leading-relaxed">
              Are you sure you want to cancel and remove this booking? This action is permanent.
            </p>
            <div className="flex justify-end gap-2.5 mt-2">
              <button
                type="button"
                onClick={() => setDeleteConfirmBookingId(null)}
                className="px-4 py-2 rounded-xl border border-slate-200 text-xs font-bold text-slate-600 hover:bg-slate-50 cursor-pointer bg-white"
              >Keep</button>
              <button
                type="button"
                onClick={() => {
                  const id = deleteConfirmBookingId;
                  setDeleteConfirmBookingId(null);
                  void confirmDelete(id);
                }}
                className="px-4 py-2 rounded-xl bg-[#7a0b2e] hover:bg-[#5c0822] text-white text-xs font-bold cursor-pointer"
              >Yes, Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
