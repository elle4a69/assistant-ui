import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  getServices,
  getFreeBusy,
  createBooking,
  Service,
  FreeBusySlot
} from './api';
import { Sparkles, ChevronLeft, ChevronRight, Info, X } from 'lucide-react';

// ─── Types ────────────────────────────────────────────────────────────────────

// Steps: 1=Service, 2=DateTime, 3=ClientDetails, 4=Success
type Step = 1 | 2 | 3 | 4;

// ─── Timezone Helpers ─────────────────────────────────────────────────────────

const TZ = 'Australia/Melbourne';

const isValidAustralianMobile = (value: string) => {
  const digits = value.replace(/\D/g, '');
  return /^04\d{8}$/.test(digits)
    || /^614\d{8}$/.test(digits)
    || /^6104\d{8}$/.test(digits)
    || /^4\d{8}$/.test(digits);
};

const melbourneFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: TZ,
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
});

/** Returns YYYY-MM-DD and HH:MM for an ISO string, in Melbourne time */
const getSlotMelbourneParts = (isoString: string) => {
  const parts = melbourneFormatter.formatToParts(new Date(isoString));
  const v = (t: string) => parts.find(p => p.type === t)?.value ?? '';
  return {
    dateStr: `${v('year')}-${v('month')}-${v('day')}`,
    timeStr: `${v('hour')}:${v('minute')}`,
  };
};

const timeToMinutes = (timeStr: string) => {
  const [h, m] = timeStr.split(':').map(Number);
  return h * 60 + m;
};

/** Returns 0=Sun … 6=Sat weekday for year/month/day in Melbourne TZ */
const getMelbourneWeekday = (year: number, month: number, day: number) => {
  const fmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'short' });
  const name = fmt.format(new Date(Date.UTC(year, month, day, 12)));
  return ({ Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 } as Record<string, number>)[name] ?? 0;
};

const getDaysInMonth = (year: number, month: number) => new Date(year, month + 1, 0).getDate();

/** Returns { year, month (0-based) } for a Date in Melbourne TZ */
const getMelbourneYearMonth = (date: Date) => {
  const fmt = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit' });
  const parts = fmt.formatToParts(date);
  return {
    year: Number(parts.find(p => p.type === 'year')?.value ?? new Date().getFullYear()),
    month: Number(parts.find(p => p.type === 'month')?.value ?? new Date().getMonth() + 1) - 1,
  };
};

interface CalendarCell {
  year: number; month: number; day: number;
  isCurrentMonth: boolean; dateStr: string;
}

/** Generates a 42-cell (6-week) calendar grid entirely in Melbourne TZ */
const getCalendarCells = (year: number, month: number): CalendarCell[] => {
  const startWd = getMelbourneWeekday(year, month, 1);
  const numDays = getDaysInMonth(year, month);
  const prevMonth = month === 0 ? 11 : month - 1;
  const prevYear = month === 0 ? year - 1 : year;
  const numPrevDays = getDaysInMonth(prevYear, prevMonth);
  const nextMonth = month === 11 ? 0 : month + 1;
  const nextYear = month === 11 ? year + 1 : year;

  const cells: CalendarCell[] = [];
  // leading
  for (let i = startWd - 1; i >= 0; i--) {
    const d = numPrevDays - i;
    cells.push({ year: prevYear, month: prevMonth, day: d, isCurrentMonth: false,
      dateStr: `${prevYear}-${String(prevMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}` });
  }
  // current
  for (let i = 1; i <= numDays; i++) {
    cells.push({ year, month, day: i, isCurrentMonth: true,
      dateStr: `${year}-${String(month + 1).padStart(2, '0')}-${String(i).padStart(2, '0')}` });
  }
  // trailing
  for (let i = 1; i <= 42 - cells.length; i++) {
    cells.push({ year: nextYear, month: nextMonth, day: i, isCurrentMonth: false,
      dateStr: `${nextYear}-${String(nextMonth + 1).padStart(2, '0')}-${String(i).padStart(2, '0')}` });
  }
  return cells;
};

/** Formats an ISO string to "DD.MM.YYYY H:MM AM/PM" in Melbourne TZ */
const formatConfirmDate = (isoString: string) => {
  const fmt = new Intl.DateTimeFormat('en-AU', {
    timeZone: TZ, day: '2-digit', month: '2-digit', year: 'numeric',
    hour: 'numeric', minute: '2-digit', hour12: true,
  });
  const parts = fmt.formatToParts(new Date(isoString));
  const v = (t: string) => parts.find(p => p.type === t)?.value ?? '';
  return `${v('day')}.${v('month')}.${v('year')} ${v('hour')}:${v('minute')} ${v('dayPeriod').toUpperCase()}`;
};

/** Formats current time as "H:MM AM/PM Australia/Melbourne" */
const getMelbourneTime = () =>
  new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: TZ }) + ' Australia/Melbourne';

/** Returns today's date in YYYY-MM-DD format in Melbourne TZ */
const getMelbourneTodayDateStr = () => {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: TZ,
    year: 'numeric', month: '2-digit', day: '2-digit'
  });
  const parts = fmt.formatToParts(new Date());
  const v = (t: string) => parts.find(p => p.type === t)?.value ?? '';
  return `${v('year')}-${v('month')}-${v('day')}`;
};

/** Returns current time in HH:MM format in Melbourne TZ */
const getMelbourneTimeStr = () => {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: TZ,
    hour: '2-digit', minute: '2-digit', hour12: false
  });
  const parts = fmt.formatToParts(new Date());
  const v = (t: string) => parts.find(p => p.type === t)?.value ?? '';
  return `${v('hour')}:${v('minute')}`;
};

const MONTH_NAMES = ['January','February','March','April','May','June','July','August','September','October','November','December'];

// ─── Potential time slots grid (24 hours, every 15 min) ───────────────────────
const POTENTIAL_TIMES: string[] = (() => {
  const times: string[] = [];
  for (let h = 0; h < 24; h++) {
    for (let m = 0; m < 60; m += 15) {
      times.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`);
    }
  }
  return times;
})();

/** Formats "HH:MM" 24h to "H:MM AM/PM" */
const fmt12h = (timeStr: string) => {
  const [h, m] = timeStr.split(':').map(Number);
  const ampm = h >= 12 ? 'PM' : 'AM';
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${ampm}`;
};

// ─── Reusable step-fade wrapper ───────────────────────────────────────────────
function StepPane({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{ animation: 'stepFadeIn 0.22s ease both' }}
      className="flex flex-col gap-4 pb-4"
    >
      {children}
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function CustomerBookingView({ embedded = false }: { embedded?: boolean }) {
  const [step, setStep] = useState<Step>(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Data
  const [services, setServices] = useState<Service[]>([]);
  const [freeSlots, setFreeSlots] = useState<FreeBusySlot[]>([]);

  // Selections
  const [selectedService, setSelectedService] = useState<Service | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<FreeBusySlot | null>(null);
  const [expandedServiceId, setExpandedServiceId] = useState<Service['id'] | null>(null);

  // Client form
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('+61');
  const [notes, setNotes] = useState('');
  const [nameError, setNameError] = useState('');
  const [phoneError, setPhoneError] = useState('');
  const [touched, setTouched] = useState({ name: false, phone: false });

  // Calendar nav
  const [viewYear, setViewYear] = useState(new Date().getFullYear());
  const [viewMonth, setViewMonth] = useState(new Date().getMonth());
  const [selectedDateStr, setSelectedDateStr] = useState<string | null>(null);

  // Misc
  const [confirmationSms, setConfirmationSms] = useState('');
  const [confirmationWarning, setConfirmationWarning] = useState('');
  const [showInfo, setShowInfo] = useState(false);
  const [nowTime, setNowTime] = useState(getMelbourneTime());
  const scrollRef = useRef<HTMLDivElement>(null);
  const timeSlotsScrollRef = useRef<HTMLDivElement>(null);
  const nowLineRef = useRef<HTMLDivElement>(null);

  // Tick clock every minute
  useEffect(() => {
    const id = setInterval(() => setNowTime(getMelbourneTime()), 60_000);
    return () => clearInterval(id);
  }, []);

  // Scroll to top on step change
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  }, [step]);

  // Keep the embedding iframe in sync with every layout change, including
  // asynchronously loaded services, validation errors and expanded descriptions.
  useEffect(() => {
    if (typeof window === 'undefined' || window.parent === window) return;

    let lastHeight = 0;
    let animationFrame: number | null = null;
    const updateSize = () => {
      if (animationFrame !== null) cancelAnimationFrame(animationFrame);
      animationFrame = requestAnimationFrame(() => {
        animationFrame = null;
        const height = Math.ceil(Math.max(
          document.body.scrollHeight,
          document.documentElement.scrollHeight,
        ));
        if (height === lastHeight) return;
        lastHeight = height;
        window.parent.postMessage({ event: 'updateWidgetSize', height }, '*');
      });
    };

    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(document.body);
    observer.observe(document.documentElement);
    window.addEventListener('load', updateSize);

    return () => {
      observer.disconnect();
      window.removeEventListener('load', updateSize);
      if (animationFrame !== null) cancelAnimationFrame(animationFrame);
    };
  }, []);

  // Scroll to "Now" line on today's selection
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    if (step === 2 && selectedDateStr) {
      timer = setTimeout(() => {
        const today = getMelbourneTodayDateStr();
        if (selectedDateStr === today) {
          if (timeSlotsScrollRef.current && nowLineRef.current) {
            const container = timeSlotsScrollRef.current;
            const target = nowLineRef.current;
            container.scrollTo({
              top: target.offsetTop - 10,
              behavior: 'smooth'
            });
          }
        } else {
          if (timeSlotsScrollRef.current) {
            timeSlotsScrollRef.current.scrollTop = 0;
          }
        }
      }, 120);
    }
    return () => {
      if (timer) clearTimeout(timer);
    };
  }, [selectedDateStr, step]);

  // Load services on mount
  useEffect(() => {
    getServices()
      .then(setServices)
      .catch(() => setError('Failed to load services. Please check backend connection.'));
  }, []);

  // Load free slots when entering step 2 (DateTime selection)
  useEffect(() => {
    if (step !== 2) return;
    setLoading(true);
    setError(null);
    getFreeBusy(selectedService?.duration)
      .then(data => {
        setFreeSlots(data);
        if (data.length > 0) {
          const byDate: Record<string, true> = {};
          data.forEach(s => { byDate[getSlotMelbourneParts(s.startTime).dateStr] = true; });
          const firstDate = Object.keys(byDate).sort()[0];
          setSelectedDateStr(firstDate);
          const { year, month } = getMelbourneYearMonth(new Date(data[0].startTime));
          setViewYear(year);
          setViewMonth(month);
        }
      })
      .catch(() => setError('Failed to fetch availability. Please try again.'))
      .finally(() => setLoading(false));
  }, [step, selectedService?.duration]);

  // Validate name
  useEffect(() => {
    if (!touched.name) return;
    setNameError(name.trim() ? '' : 'Name is required');
  }, [name, touched.name]);

  // Validate phone
  useEffect(() => {
    if (!touched.phone) return;
    if (!phone.trim()) {
      setPhoneError('Phone number is required');
    } else if (!isValidAustralianMobile(phone)) {
      setPhoneError('Enter a valid Australian mobile, e.g. 0412 345 678');
    } else {
      setPhoneError('');
    }
  }, [phone, touched.phone]);

  // Scroll to top when step changes
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [step]);

  // Pre-parse slots for fast lookup to prevent lag (500,000x speed improvement!)
  const preparsedSlots = useMemo(() => {
    return freeSlots.map(s => {
      const { dateStr, timeStr } = getSlotMelbourneParts(s.startTime);
      return { slot: s, dateStr, timeStr };
    });
  }, [freeSlots]);

  const slotsByDate = useMemo(() => {
    const byDate: Record<string, FreeBusySlot[]> = {};
    preparsedSlots.forEach(ps => {
      if (!byDate[ps.dateStr]) byDate[ps.dateStr] = [];
      byDate[ps.dateStr].push(ps.slot);
    });
    return byDate;
  }, [preparsedSlots]);

  const getMatchingSlot = useCallback((dateStr: string, timeStr: string) => {
    const ps = preparsedSlots.find(ps => ps.dateStr === dateStr && ps.timeStr === timeStr);
    return ps ? ps.slot : null;
  }, [preparsedSlots]);

  const totalAmount = selectedService?.price ?? 0;

  /** Which "visible" tab step is active: 1=Service, 2=Time, 3=Client */
  const navActive = step <= 3 ? step : 3;

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleReset = () => {
    setStep(1);
    setSelectedService(null);
    setSelectedSlot(null);
    setName('');
    setPhone('+61');
    setNotes('');
    setNameError('');
    setPhoneError('');
    setTouched({ name: false, phone: false });
    setConfirmationSms('');
    setConfirmationWarning('');
    setError(null);
    setFreeSlots([]);
    setSelectedDateStr(null);
  };

  const handleInputFocus = () => {
    setTimeout(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTo({
          top: scrollRef.current.scrollHeight,
          behavior: 'smooth'
        });
      }
    }, 150);
  };

  const handleSubmit = async () => {
    // Force validation
    setTouched({ name: true, phone: true });
    const nameOk = name.trim().length > 0;
    const phoneOk = isValidAustralianMobile(phone);
    if (!nameOk) setNameError('Name is required');
    if (!phoneOk) setPhoneError('Enter a valid Australian mobile, e.g. 0412 345 678');
    if (!nameOk || !phoneOk || !selectedService || !selectedSlot) return;

    setLoading(true);
    setError(null);
    try {
      const res = await createBooking({
        serviceId: selectedService.id,
        name,
        phone,
        startTime: selectedSlot.startTime,
        notes: notes || undefined,
      });
      setConfirmationSms(res.smsSent);
      setConfirmationWarning(res.smsError || '');
      setStep(4);
    } catch (err: any) {
      setError(err?.message ?? 'Booking failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const prevMonth = () => {
    if (viewMonth === 0) { setViewMonth(11); setViewYear(y => y - 1); }
    else setViewMonth(m => m - 1);
  };
  const nextMonth = () => {
    if (viewMonth === 11) { setViewMonth(0); setViewYear(y => y + 1); }
    else setViewMonth(m => m + 1);
  };

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <>
      {/* Inject keyframe once */}
      <style>{`
        @keyframes stepFadeIn {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: translateY(0);    }
        }
        .booking-scroll::-webkit-scrollbar { width: 4px; }
        .booking-scroll::-webkit-scrollbar-track { background: transparent; }
        .booking-scroll::-webkit-scrollbar-thumb { background: #f5d5de; border-radius: 99px; }
        .booking-crimson-outline {
          border: 1px solid #d2143a !important;
          outline: none;
        }
      `}</style>

      {/*
        OUTER WRAPPER:
        - Takes 100% of whatever container it sits in (standalone or native embed).
        - No min-h-screen, no fixed positioning.
        - Flex column so header + nav are fixed at top, content scrolls.
      */}
      <div
        className={`relative w-full min-h-0 flex flex-col font-sans text-slate-800 antialiased select-none ${embedded ? 'bg-transparent' : 'h-full bg-[#faf6f6]'}`}
        style={{ fontFamily: "'Inter', system-ui, sans-serif" }}
      >

        {/* ── Header ──────────────────────────────────────────────────────── */}
        {!embedded && <div className="bg-white border-b border-slate-100 px-4 py-3 flex items-center justify-between shrink-0 z-10">
          <button
            onClick={handleReset}
            className="text-[#7a0b2e] font-serif italic font-extrabold text-xl tracking-tight cursor-pointer bg-transparent border-none p-0"
          >
            Tori
          </button>

          <div className="flex items-center gap-2">
            {/* Online badge */}
            <span className="flex items-center gap-1 bg-[#7a0b2e]/10 px-2 py-0.5 rounded-full text-[#7a0b2e] text-[9px] font-bold border border-[#7a0b2e]/20">
              <span className="w-1.5 h-1.5 bg-[#7a0b2e] rounded-full animate-pulse inline-block" />
              online
            </span>

            {/* Reset */}
            <button
              onClick={handleReset}
              title="Start over"
              className="p-1.5 text-slate-400 hover:text-[#7a0b2e] transition-colors cursor-pointer bg-transparent border-none"
            >
              <svg className="w-3.5 h-3.5 stroke-[2.5]" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38" />
              </svg>
            </button>

            {/* Info */}
            <button
              onClick={() => setShowInfo(true)}
              title="Booking information"
              className="p-1.5 text-slate-400 hover:text-[#7a0b2e] transition-colors cursor-pointer bg-transparent border-none"
            >
              <Info className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>}

        {/* ── Step Nav ────────────────────────────────────────────────────── */}
        {step < 4 && (
          <div className={`${embedded ? 'bg-transparent border-b border-slate-300/40 px-0 mb-3' : 'bg-white border-b border-slate-100 px-5'} py-0 shrink-0 z-10`}>
            <div className="flex">
              {[
                { label: 'Service', nav: 1 },
                { label: 'Time',    nav: 2 },
                { label: 'Client',  nav: 3 },
              ].map(({ label, nav }, i, arr) => (
                <div key={nav} className="flex items-center flex-1">
                  <button
                    onClick={() => {
                      if (nav === 1 && step > 1) setStep(1);
                      if (nav === 2 && step > 2) setStep(2);
                      // can't go forward
                    }}
                    disabled={nav > navActive}
                    className={`
                      flex-1 py-3 text-center text-xs sm:text-sm font-extrabold border-b-2 transition-colors cursor-pointer bg-transparent
                      ${nav === navActive
                        ? 'border-[#7a0b2e] text-[#7a0b2e]'
                        : nav < navActive
                          ? 'border-transparent text-slate-600 hover:text-slate-800'
                          : 'border-transparent text-slate-400 cursor-default'
                      }
                    `}
                  >
                    {label}
                  </button>
                  {i < arr.length - 1 && (
                    <div className="h-3 w-px bg-slate-150 shrink-0" />
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Time Banner ─────────────────────────────────────────────────── */}
        {step < 4 && (
          <div className={`text-center py-1.5 text-[10px] sm:text-xs font-bold tracking-wider uppercase shrink-0 ${embedded ? 'text-slate-500 bg-transparent mb-3' : 'text-slate-500 bg-[#fafaf7] border-b border-slate-100'}`}>
            Our time: {nowTime}
          </div>
        )}

        {/* ── Scrollable Content ──────────────────────────────────────────── */}
        <div
          ref={scrollRef}
          className={`flex-1 booking-scroll ${embedded ? 'overflow-visible p-0 pb-4' : 'overflow-y-auto p-4 pb-40'}`}
        >
          {/* Global error */}
          {error && (
            <div className="mb-4 p-3 bg-rose-50 border border-rose-200 text-rose-700 text-xs rounded-xl font-semibold flex items-start gap-2">
              <svg className="w-4 h-4 text-rose-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              {error}
            </div>
          )}

          {/* ── STEP 1: Service ─────────────────────────────────────────── */}
          {step === 1 && (
            <StepPane>
              {services.length === 0 && !error ? (
                <div className="booking-crimson-outline rounded-xl bg-slate-950/80 py-16 text-center text-sm font-bold text-slate-300">
                  <div className="w-8 h-8 border-2 border-[#7a0b2e]/30 border-t-[#7a0b2e] rounded-full animate-spin mx-auto mb-3" />
                  Loading services…
                </div>
              ) : services.length === 0 ? null : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {services.map(srv => (
                    <article
                      key={srv.id}
                      className="booking-crimson-outline group relative overflow-hidden rounded-xl bg-slate-950/80 shadow-sm transition-[transform,box-shadow] duration-150 hover:scale-[1.01] hover:shadow-md"
                    >
                      <button
                        type="button"
                        aria-label={`Select ${srv.name}`}
                        onClick={() => { setSelectedService(srv); setStep(2); }}
                        className="absolute inset-0 z-0 cursor-pointer rounded-xl bg-transparent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#d2143a]"
                      />

                      <div className="relative z-10 pointer-events-none flex flex-col gap-3 p-4">
                        <h3 className="text-balance text-sm font-extrabold leading-snug text-[#d2143a] sm:text-base">{srv.name}</h3>
                        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                          <p className={`text-pretty text-xs font-medium leading-relaxed text-slate-300 ${expandedServiceId === srv.id ? '' : 'line-clamp-4'}`}>
                            {srv.description}
                          </p>
                          {srv.description && srv.description.length > 140 && (
                            <button
                              type="button"
                              onClick={() => setExpandedServiceId(current => current === srv.id ? null : srv.id)}
                              aria-expanded={expandedServiceId === srv.id}
                              className="pointer-events-auto self-start border-none bg-transparent p-0 text-[11px] font-bold text-rose-300 hover:text-white hover:underline cursor-pointer"
                            >
                              {expandedServiceId === srv.id ? 'Less' : 'More'}
                            </button>
                          )}
                        </div>

                        <div className="flex items-center justify-between gap-2 border-t border-white/10 pt-2.5">
                          {srv.showDuration !== false && (
                            <span className="text-xs font-bold text-slate-400">
                              {srv.duration >= 60 ? `${srv.duration / 60} hr.` : `${srv.duration} min.`}
                            </span>
                          )}
                          <div className="ml-auto flex items-center gap-2.5">
                            <span className="tabular-nums text-sm font-extrabold text-white sm:text-base">AU${srv.price}</span>
                            <span className="rounded-md border border-rose-300/20 bg-[#7a0b2e] px-3 py-1.5 text-xs font-bold text-white transition-colors group-hover:bg-[#92123b]">Select</span>
                          </div>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </StepPane>
          )}
          {/* ── STEP 2: Date & Time ─────────────────────────────────────── */}
          {step === 2 && (
            <StepPane>
              <button
                onClick={() => setStep(1)}
                className="flex items-center gap-1 text-[#d2143a] text-xs font-bold hover:underline cursor-pointer bg-transparent border-none p-0 self-start"
              >
                <ChevronLeft className="w-3.5 h-3.5" /> back
              </button>

              {loading ? (
                <div className="booking-crimson-outline rounded-xl bg-slate-950/80 py-16 text-center text-xs font-bold text-slate-300">
                  <div className="w-8 h-8 border-2 border-[#7a0b2e]/30 border-t-[#7a0b2e] rounded-full animate-spin mx-auto mb-3" />
                  Checking availability…
                </div>
              ) : freeSlots.length === 0 ? (
                <div className="booking-crimson-outline rounded-xl bg-slate-950/80 py-16 text-center text-xs font-bold text-slate-300">
                  No availability right now. Please check back soon.
                </div>
              ) : (
                <>
                  {/* Calendar */}
                  <div className="booking-crimson-outline rounded-xl bg-slate-950/80 p-3.5 text-slate-100 shadow-sm">
                    {/* Month header */}
                    <div className="flex justify-between items-center mb-3 px-0.5">
                      <button
                        onClick={prevMonth}
                        className="flex items-center gap-0.5 text-xs font-bold text-[#d2143a] hover:underline cursor-pointer bg-transparent border-none p-0"
                      >
                        <ChevronLeft className="w-3.5 h-3.5" /> Prev
                      </button>
                      <span className="text-balance text-sm font-extrabold text-white">
                        {MONTH_NAMES[viewMonth]} {viewYear}
                      </span>
                      <button
                        onClick={nextMonth}
                        className="flex items-center gap-0.5 text-xs font-bold text-[#d2143a] hover:underline cursor-pointer bg-transparent border-none p-0"
                      >
                        Next <ChevronRight className="w-3.5 h-3.5" />
                      </button>
                    </div>

                    {/* Day headers */}
                    <div className="grid grid-cols-7 text-center text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1">
                      {['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].map(d => <span key={d}>{d}</span>)}
                    </div>

                    {/* Day cells */}
                    <div className="grid grid-cols-7 gap-y-1 justify-items-center">
                      {getCalendarCells(viewYear, viewMonth).map((cell, idx) => {
                        const hasSlots = !!slotsByDate[cell.dateStr];
                        const isSelected = selectedDateStr === cell.dateStr;

                        let cls = 'w-9 h-9 flex items-center justify-center text-xs rounded-full transition-all font-semibold ';
                        if (!cell.isCurrentMonth) {
                          cls += 'text-slate-200 pointer-events-none opacity-0';
                        } else if (isSelected) {
                          cls += 'bg-[#d2143a] text-white font-bold shadow-sm';
                        } else if (hasSlots) {
                          cls += 'bg-[#d2143a]/15 border border-[#d2143a] text-rose-300 font-black hover:bg-[#d2143a]/25 cursor-pointer';
                        } else {
                          cls += 'text-slate-600 bg-slate-900/60 pointer-events-none opacity-50';
                        }

                        return (
                          <button
                            key={idx}
                            type="button"
                            onClick={() => hasSlots && setSelectedDateStr(cell.dateStr)}
                            disabled={!cell.isCurrentMonth || !hasSlots}
                            className={cls}
                          >
                            {cell.day}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Time slots */}
                  {selectedDateStr && (() => {
                    const today = getMelbourneTodayDateStr();
                    const isTodaySelected = selectedDateStr === today;
                    const currentTimeStr = getMelbourneTimeStr();
                    const currentMinutes = timeToMinutes(currentTimeStr);
                    const nowIndex = POTENTIAL_TIMES.findIndex(t => timeToMinutes(t) >= currentMinutes);
                    const nowLabel = new Date().toLocaleTimeString('en-US', {
                      timeZone: TZ,
                      hour: 'numeric',
                      minute: '2-digit',
                      hour12: true
                    });

                    return (
                      <div className="booking-crimson-outline flex flex-col gap-3 rounded-xl bg-slate-950/80 p-4 shadow-sm">
                        <span className="text-[11px] font-bold text-slate-300 uppercase tracking-wide">
                          Available Start Times
                        </span>
                        
                        <div
                          ref={timeSlotsScrollRef}
                          className="relative max-h-[250px] overflow-y-auto pr-1 select-none scroll-smooth"
                        >
                          <div className="grid grid-cols-3 gap-2 relative">
                            {POTENTIAL_TIMES.map((t, idx) => {
                              const match = getMatchingSlot(selectedDateStr, t);
                              const selectable = !!match;
                              
                              // Determine if this time block is covered by the selected slot's duration
                              const isSelected = !!selectedSlot && (() => {
                                const selParts = getSlotMelbourneParts(selectedSlot.startTime);
                                if (selParts.dateStr !== selectedDateStr) return false;
                                const selectedStartMin = timeToMinutes(selParts.timeStr);
                                const selectedEndMin = selectedStartMin + (selectedService?.duration ?? 30);
                                const currentMin = timeToMinutes(t);
                                return currentMin >= selectedStartMin && currentMin < selectedEndMin;
                              })();

                              let cls = 'py-2.5 text-xs font-bold rounded-lg border text-center transition-all ';
                              if (isSelected) {
                                cls += 'bg-white border-white text-slate-950 font-black shadow-md ring-2 ring-[#d2143a] scale-102';
                              } else if (selectable) {
                                cls += 'border-[#7a0b2e] text-white bg-[#7a0b2e] hover:bg-[#5c0822] cursor-pointer font-black shadow-xs';
                              } else {
                                cls += 'bg-slate-900 border-slate-800 text-slate-600 pointer-events-none';
                              }

                              return (
                                <div key={idx} className="contents">
                                  {isTodaySelected && idx === nowIndex && (
                                    <div
                                      ref={nowLineRef}
                                      className="col-span-full flex items-center gap-2 my-2 py-1 select-none"
                                    >
                                      <span className="w-1.5 h-1.5 rounded-full bg-[#7a0b2e] animate-pulse shrink-0"></span>
                                      <span className="text-[10px] font-black text-[#7a0b2e] uppercase tracking-wider whitespace-nowrap">
                                        Now • {nowLabel}
                                      </span>
                                      <div className="flex-1 h-[2px] bg-[#7a0b2e]/20"></div>
                                    </div>
                                  )}
                                  
                                  <button
                                    type="button"
                                    disabled={!selectable}
                                    onClick={() => match && setSelectedSlot(match)}
                                    className={cls}
                                  >
                                    {fmt12h(t)}
                                  </button>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      </div>
                    );
                  })()}

                  <button
                    type="button"
                    disabled={!selectedSlot}
                    onClick={() => setStep(3)}
                    className="self-end bg-[#7a0b2e] hover:bg-[#5c0822] active:bg-[#450518] disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-bold px-7 py-3 rounded-full shadow-sm transition-colors cursor-pointer"
                  >
                    Next →
                  </button>
                </>
              )}
            </StepPane>
          )}

          {/* ── STEP 3: Client Details & Confirm ────────────────────────── */}
          {step === 3 && (
            <StepPane>
              <button
                onClick={() => setStep(2)}
                className="flex items-center gap-1 text-[#d2143a] text-xs font-bold hover:underline cursor-pointer bg-transparent border-none p-0 self-start"
              >
                <ChevronLeft className="w-3.5 h-3.5" /> back
              </button>

              <h2 className="text-balance text-base font-bold text-white">Please, confirm details</h2>

              <div className="booking-crimson-outline overflow-hidden rounded-xl bg-slate-950/80 text-slate-100 shadow-sm">
                {/* Form */}
                <div className="flex flex-col gap-3 border-b border-white/10 p-4">
                  <div className="flex flex-col gap-1">
                    <label className="text-[11px] font-bold text-slate-300 uppercase tracking-wider">
                      Name <span className="text-rose-500">*</span>
                    </label>
                    <input
                      type="text"
                      value={name}
                      onChange={e => setName(e.target.value)}
                      onBlur={() => setTouched(t => ({ ...t, name: true }))}
                      onFocus={handleInputFocus}
                      placeholder="Your full name"
                      className={`w-full rounded-lg border bg-slate-900 p-3 text-sm font-semibold text-white placeholder:text-slate-500 focus:outline-none focus:ring-1 transition-colors ${
                        nameError ? 'border-rose-400 focus:ring-rose-400 bg-rose-950/40' : 'border-slate-600 focus:ring-[#d2143a] focus:border-[#d2143a]'
                      }`}
                    />
                    {nameError && (
                      <span className="mt-0.5 text-xs font-semibold text-rose-300">{nameError}</span>
                    )}
                  </div>

                  <div className="flex flex-col gap-1">
                    <label className="text-[11px] font-bold text-slate-300 uppercase tracking-wider">
                      Phone <span className="text-rose-500">*</span>
                    </label>
                    <input
                      type="tel"
                      value={phone}
                      onChange={e => {
                        // Accept local 04xx entry after the prefilled +61 without
                        // retaining the Australian trunk zero as +6104xx.
                        setPhone(e.target.value.replace(/^(\+61[\s-]*)0/, '$1'));
                      }}
                      onBlur={() => setTouched(t => ({ ...t, phone: true }))}
                      onFocus={handleInputFocus}
                      placeholder="+61 400 000 000"
                      className={`w-full rounded-lg border bg-slate-900 p-3 text-sm font-semibold text-white placeholder:text-slate-500 focus:outline-none focus:ring-1 transition-colors ${
                        phoneError ? 'border-rose-400 focus:ring-rose-400 bg-rose-950/40' : 'border-slate-600 focus:ring-[#d2143a] focus:border-[#d2143a]'
                      }`}
                    />
                    {phoneError && (
                      <span className="mt-0.5 text-xs font-semibold text-rose-300">{phoneError}</span>
                    )}
                  </div>
                </div>

                {/* Booking Summary */}
                {selectedService && selectedSlot && (
                  <div className="flex flex-col gap-2 p-4 text-xs font-semibold text-slate-200">
                    <div className="text-balance text-sm font-extrabold text-[#d2143a]">{selectedService.name}</div>

                    <div className="flex justify-between">
                      <span className="text-slate-400">Date:</span>
                      <span className="font-bold text-white">{formatConfirmDate(selectedSlot.startTime)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">Provider:</span>
                      <span className="font-bold text-white">Tori</span>
                    </div>

                    <div className="my-1 border-t border-white/10" />

                    <div className="mb-1 text-[11px] font-bold text-slate-400 uppercase tracking-wider">Items:</div>

                    <div className="flex justify-between">
                      <span className="font-semibold text-slate-200">{selectedService.name}</span>
                      <span className="tabular-nums text-white">AU${selectedService.price}.00</span>
                    </div>

                    <div className="mt-1 flex items-center justify-between border-t border-white/10 pt-2 text-base font-extrabold text-[#d2143a]">
                      <span>Total for booking:</span>
                      <span className="tabular-nums">AU${totalAmount}.00</span>
                    </div>
                  </div>
                )}

                {/* Submit */}
                <div className="px-4 pb-4">
                  <button
                    type="button"
                    disabled={loading}
                    onClick={handleSubmit}
                    className="w-full bg-[#7a0b2e] hover:bg-[#5c0822] active:bg-[#450518] disabled:opacity-50 text-white py-2.5 rounded-xl text-xs font-bold uppercase tracking-wider transition-colors cursor-pointer flex justify-center items-center gap-2"
                  >
                    {loading ? (
                      <>
                        <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                        Processing…
                      </>
                    ) : 'Confirm booking'}
                  </button>
                </div>
              </div>
            </StepPane>
          )}

          {/* ── STEP 4: Success ──────────────────────────────────────────── */}
          {step === 4 && (
            <StepPane>
              <div className="booking-crimson-outline flex flex-col items-center gap-5 rounded-xl bg-slate-950/80 px-5 py-6 text-center text-slate-100">
                {/* Visual Representation of Tori */}
                <div className="relative select-none my-1 shrink-0">
                  {/* Outer pulsing ring */}
                  <div className="absolute inset-0 rounded-full bg-[#7a0b2e]/10 animate-ping" style={{ animationDuration: '3s' }}></div>
                  {/* Avatar image container */}
                  <div className="booking-crimson-outline relative size-20 overflow-hidden rounded-full shadow-md">
                    <img 
                      src="/tori_avatar.jpg" 
                      alt="Tori" 
                      className="size-full object-cover"
                    />
                  </div>
                  
                  {/* Online/Verified badge */}
                  <span className="absolute bottom-0 right-0 flex size-5.5 items-center justify-center rounded-full border-2 border-white bg-emerald-500 shadow-xs">
                    <span className="size-1.5 rounded-full bg-white animate-pulse"></span>
                  </span>
                </div>

                <div>
                  <h2 className="text-balance mb-1 text-lg font-bold text-[#d2143a]">Booking Confirmed!</h2>
                  <p className="text-pretty max-w-[280px] text-[11px] font-semibold leading-relaxed text-slate-300">
                    <span className="font-extrabold text-white">{name}</span>, your booking with me on <span className="font-extrabold text-white">{selectedSlot ? formatConfirmDate(selectedSlot.startTime) : ''}</span> is confirmed!<br /><br />
                    {confirmationWarning ? 'Your booking is saved. Please message me directly for the address details.' : "You'll receive a confirmation SMS with the address details shortly."}
                  </p>
                </div>

                {confirmationWarning && (
                  <div className="w-full max-w-[300px] rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-semibold text-amber-800">
                    {confirmationWarning}
                  </div>
                )}

                {/* SMS preview card */}
                {confirmationSms && (
                  <div className="booking-crimson-outline flex w-full max-w-[300px] flex-col gap-2.5 rounded-2xl bg-[#0e0f1a] p-3.5 shadow-lg">
                    <div className="flex items-center gap-1.5 text-[9px] font-bold text-gray-400 border-b border-gray-800 pb-2">
                      <svg className="w-3.5 h-3.5 text-[#7a0b2e]" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M17 2H7C5.9 2 5 2.9 5 4v16l7-3 7 3V4c0-1.1-.9-2-2-2z"/>
                      </svg>
                      <span>Messages · Just now</span>
                    </div>
                    <div className="flex gap-2 items-start">
                      <div className="w-6 h-6 rounded-full bg-[#7a0b2e] flex items-center justify-center text-[9px] font-bold text-white shrink-0">
                        T
                      </div>
                      <div className="bg-[#1c1e2e] text-gray-200 text-[9px] p-2.5 rounded-xl rounded-tl-none border border-gray-800 leading-relaxed whitespace-pre-line font-medium max-w-[85%]">
                        {confirmationSms}
                      </div>
                    </div>
                  </div>
                )}

                <button
                  onClick={handleReset}
                  className="bg-[#7a0b2e] hover:bg-[#5c0822] text-white text-xs font-bold px-6 py-2.5 rounded-xl shadow-sm transition-colors cursor-pointer"
                >
                  Book Another Appointment
                </button>
              </div>
            </StepPane>
          )}
        </div>

        {/* ── Info Modal ──────────────────────────────────────────────────── */}
        {/*
          Uses absolute positioning within the widget container (not fixed),
          so it works correctly inside iframes.
        */}
        {showInfo && (
          <div
            className="absolute inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4 z-50"
            onClick={() => setShowInfo(false)}
          >
            <div
              className="booking-crimson-outline flex w-full max-w-[300px] flex-col gap-4 rounded-2xl bg-slate-950 p-5 text-slate-100 shadow-2xl"
              onClick={e => e.stopPropagation()}
            >
              <div className="flex items-center justify-between">
                <h3 className="text-balance flex items-center gap-1.5 text-sm font-bold text-[#d2143a]">
                  <Sparkles className="w-4 h-4 text-amber-500" />
                  Booking Information
                </h3>
                <button aria-label="Close booking information" onClick={() => setShowInfo(false)} className="text-slate-400 hover:text-white cursor-pointer bg-transparent border-none p-0">
                  <X className="w-4 h-4" />
                </button>
              </div>
              <p className="text-pretty text-xs font-semibold leading-relaxed text-slate-300">
                Welcome to Tori's booking assistant.<br /><br />
                • Services are incall sessions located in Noble Park.<br />
                • All selections are discrete and secure.<br />
                • Cash or cards are accepted on arrival.<br />
                • You will receive SMS verification upon final confirmation.
              </p>
              <button
                onClick={() => setShowInfo(false)}
                className="w-full bg-[#7a0b2e] hover:bg-[#5c0822] text-white py-2 rounded-xl text-xs font-bold uppercase tracking-wider transition-colors cursor-pointer"
              >
                Got it
              </button>
            </div>
          </div>
        )}

      </div>
    </>
  );
}
