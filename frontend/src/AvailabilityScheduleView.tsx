import { useEffect, useState } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Check,
  CheckCircle2,
  AlertCircle,
  X,
  Loader2,
  RotateCcw,
  ArrowLeft,
} from 'lucide-react';
import {
  AvailabilityConfig,
  AvailabilitySlotDay,
  getAvailabilityConfig,
  saveAvailabilityConfig,
} from './api';

export interface SlotDef {
  timeStr: string; // "09:00"
  label: string;   // "9:00 AM"
  hour: number;
  minute: number;
}

export const ALL_HALF_HOUR_SLOTS: SlotDef[] = Array.from({ length: 48 }, (_, i) => {
  const hour = Math.floor(i / 2);
  const minute = (i % 2) * 30;
  const timeStr = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
  const period = hour >= 12 ? 'PM' : 'AM';
  const displayHour = hour === 0 ? 12 : hour > 12 ? hour - 12 : hour;
  const displayMinute = minute === 0 ? ':00' : ':30';
  const label = `${displayHour}${displayMinute} ${period}`;
  return { timeStr, label, hour, minute };
});

export const STANDARD_BUSINESS_SLOTS = [
  '09:00', '09:30', '10:00', '10:30', '11:00', '11:30',
  '12:00', '12:30', '13:00', '13:30', '14:00', '14:30',
  '15:00', '15:30', '16:00', '16:30'
];

export const ALL_DAY_SLOTS = ALL_HALF_HOUR_SLOTS.map(s => s.timeStr);

const DAYS_OF_WEEK = [
  'Sunday',
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday'
] as const;

const DAY_SHORT_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;

const DEFAULT_WEEKLY_HOURS: AvailabilitySlotDay[] = [
  { day: 'Sunday', enabled: false, slots: [] },
  { day: 'Monday', enabled: true, slots: [...STANDARD_BUSINESS_SLOTS] },
  { day: 'Tuesday', enabled: true, slots: [...STANDARD_BUSINESS_SLOTS] },
  { day: 'Wednesday', enabled: true, slots: [...STANDARD_BUSINESS_SLOTS] },
  { day: 'Thursday', enabled: true, slots: [...STANDARD_BUSINESS_SLOTS] },
  { day: 'Friday', enabled: true, slots: [...STANDARD_BUSINESS_SLOTS] },
  { day: 'Saturday', enabled: false, slots: [] },
];

function getSundayOfWeek(d: Date): Date {
  const date = new Date(d);
  const day = date.getDay(); // 0 is Sunday
  const diff = date.getDate() - day;
  const sunday = new Date(date.setDate(diff));
  sunday.setHours(0, 0, 0, 0);
  return sunday;
}

function formatWeekHeader(date: Date): string {
  const month = date.toLocaleDateString('en-US', { month: 'short' });
  const day = date.getDate();
  const year = date.getFullYear();
  return `Week of ${month} ${day}, ${year}`;
}

function formatDayCardDate(weekStart: Date, dayIndex: number): string {
  const d = new Date(weekStart);
  d.setDate(weekStart.getDate() + dayIndex);
  const month = d.toLocaleDateString('en-US', { month: 'short' });
  const day = d.getDate();
  return `${month} ${day}`;
}

interface AvailabilityScheduleViewProps {
  onBackToBookings?: () => void;
}

export default function AvailabilityScheduleView({ onBackToBookings }: AvailabilityScheduleViewProps) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Week anchor date (defaults to current week's Sunday)
  const [weekStart, setWeekStart] = useState<Date>(() => getSundayOfWeek(new Date()));

  // Active day tab for mobile screens (< sm)
  const [mobileActiveDayIndex, setMobileActiveDayIndex] = useState<number>(() => {
    return new Date().getDay();
  });

  // Availability configuration
  const [maxDaysAhead, setMaxDaysAhead] = useState<number>(14);
  const [weeklyHours, setWeeklyHours] = useState<AvailabilitySlotDay[]>(DEFAULT_WEEKLY_HOURS);

  // Load config on mount
  useEffect(() => {
    let isMounted = true;
    setLoading(true);
    setError(null);

    getAvailabilityConfig()
      .then(config => {
        if (!isMounted) return;
        if (config && Array.isArray(config.weeklyHours) && config.weeklyHours.length > 0) {
          setMaxDaysAhead(typeof config.maxDaysAhead === 'number' ? config.maxDaysAhead : 14);
          // Normalize to ensure all 7 days exist in Sunday-Saturday order
          const normalized = DAYS_OF_WEEK.map(dayName => {
            const found = config.weeklyHours.find(h => h.day.toLowerCase() === dayName.toLowerCase());
            if (found) {
              return {
                day: dayName,
                enabled: Boolean(found.enabled),
                open: found.open || '09:00',
                close: found.close || '17:00',
                slots: Array.isArray(found.slots) ? [...found.slots] : [],
              };
            }
            return {
              day: dayName,
              enabled: false,
              open: '09:00',
              close: '17:00',
              slots: [],
            };
          });
          setWeeklyHours(normalized);
        } else {
          setWeeklyHours(DEFAULT_WEEKLY_HOURS);
        }
      })
      .catch(err => {
        if (!isMounted) return;
        console.warn('Could not load availability config, using standard defaults:', err);
        setWeeklyHours(DEFAULT_WEEKLY_HOURS);
      })
      .finally(() => {
        if (isMounted) setLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, []);

  // Week Navigation handlers
  const handlePrevWeek = () => {
    setWeekStart(prev => {
      const next = new Date(prev);
      next.setDate(prev.getDate() - 7);
      return next;
    });
  };

  const handleNextWeek = () => {
    setWeekStart(prev => {
      const next = new Date(prev);
      next.setDate(prev.getDate() + 7);
      return next;
    });
  };

  const handleResetCurrentWeek = () => {
    setWeekStart(getSundayOfWeek(new Date()));
  };

  // Toggle Day enabled/disabled
  const handleToggleDay = (dayIndex: number) => {
    setWeeklyHours(prev => {
      const next = [...prev];
      const target = next[dayIndex];
      if (!target) return prev;
      next[dayIndex] = {
        ...target,
        enabled: !target.enabled,
      };
      return next;
    });
  };

  // Toggle Individual Time Slot
  const handleToggleSlot = (dayIndex: number, timeStr: string) => {
    setWeeklyHours(prev => {
      const next = [...prev];
      const target = next[dayIndex];
      if (!target || !target.enabled) return prev;

      const currentSlots = new Set(target.slots);
      if (currentSlots.has(timeStr)) {
        currentSlots.delete(timeStr);
      } else {
        currentSlots.add(timeStr);
      }

      // Keep sorted by time
      const sortedSlots = ALL_HALF_HOUR_SLOTS
        .filter(s => currentSlots.has(s.timeStr))
        .map(s => s.timeStr);

      next[dayIndex] = {
        ...target,
        slots: sortedSlots,
      };
      return next;
    });
  };

  // Preset Handlers
  const handleApplyPreset = (dayIndex: number, preset: 'standard' | 'all-day' | 'clear') => {
    setWeeklyHours(prev => {
      const next = [...prev];
      const target = next[dayIndex];
      if (!target) return prev;

      let newSlots: string[] = [];
      if (preset === 'standard') {
        newSlots = [...STANDARD_BUSINESS_SLOTS];
      } else if (preset === 'all-day') {
        newSlots = [...ALL_DAY_SLOTS];
      } else if (preset === 'clear') {
        newSlots = [];
      }

      next[dayIndex] = {
        ...target,
        enabled: preset === 'clear' ? target.enabled : true,
        slots: newSlots,
      };
      return next;
    });
  };

  // Save Availability
  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccessMsg(null);

    try {
      // Derive open and close boundaries per day for backward compatibility
      const enrichedWeeklyHours: AvailabilitySlotDay[] = weeklyHours.map(day => {
        if (!day.slots || day.slots.length === 0) {
          return {
            ...day,
            open: day.open || '09:00',
            close: day.close || '17:00',
          };
        }
        const sorted = [...day.slots].sort();
        const openTime = sorted[0];
        const lastSlot = sorted[sorted.length - 1];
        // Calculate close time as last slot + 30 mins
        const [h, m] = lastSlot.split(':').map(Number);
        const closeTotalMins = h * 60 + m + 30;
        const closeH = Math.min(24, Math.floor(closeTotalMins / 60));
        const closeM = closeTotalMins % 60;
        const closeTime = `${String(closeH).padStart(2, '0')}:${String(closeM).padStart(2, '0')}`;

        return {
          ...day,
          open: openTime,
          close: closeTime,
        };
      });

      const payload: AvailabilityConfig = {
        maxDaysAhead: Math.max(1, Math.min(365, maxDaysAhead || 14)),
        weeklyHours: enrichedWeeklyHours,
      };

      await saveAvailabilityConfig(payload);
      setSuccessMsg('Availability schedule saved successfully.');
    } catch (err) {
      console.error('Failed to save availability schedule:', err);
      setError(err instanceof Error ? err.message : 'Failed to save availability schedule.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-slate-50/50 overflow-y-auto">
      {/* ── Top Bar / Header ── */}
      <div className="bg-white border-b border-slate-200 px-4 sm:px-6 py-4 shrink-0 shadow-2xs">
        <div className="max-w-5xl mx-auto flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            {onBackToBookings && (
              <button
                type="button"
                onClick={onBackToBookings}
                className="p-2 -ml-2 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded-xl transition-colors cursor-pointer"
                title="Back to Bookings"
              >
                <ArrowLeft className="w-5 h-5" />
              </button>
            )}
            <div>
              <h1 className="text-lg sm:text-xl font-extrabold text-slate-900 tracking-tight flex items-center gap-2">
                Weekly Schedule
              </h1>
              <p className="text-xs text-slate-500 font-medium mt-0.5">
                Manage your recurring weekly appointment availability and booking limits
              </p>
            </div>
          </div>

          {/* Quick Stats / Reset Week */}
          <div className="flex items-center gap-2 self-start sm:self-auto">
            <button
              type="button"
              onClick={handleResetCurrentWeek}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold text-slate-600 bg-slate-100 hover:bg-slate-200 rounded-lg transition-colors cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Current Week</span>
            </button>
          </div>
        </div>
      </div>

      {/* ── Main Container ── */}
      <div className="max-w-5xl w-full mx-auto p-4 sm:p-6 flex-1 flex flex-col gap-6">

        {/* ── Notifications ── */}
        {error && (
          <div className="p-3.5 bg-rose-50 border border-rose-200 text-rose-800 text-xs rounded-xl font-bold flex items-center justify-between shadow-2xs">
            <div className="flex items-center gap-2.5">
              <AlertCircle className="w-4 h-4 text-rose-600 shrink-0" />
              <span>{error}</span>
            </div>
            <button
              type="button"
              onClick={() => setError(null)}
              className="text-rose-400 hover:text-rose-600 cursor-pointer"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {successMsg && (
          <div className="p-3.5 bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs rounded-xl font-bold flex items-center justify-between shadow-2xs">
            <div className="flex items-center gap-2.5">
              <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
              <span>{successMsg}</span>
            </div>
            <button
              type="button"
              onClick={() => setSuccessMsg(null)}
              className="text-emerald-400 hover:text-emerald-600 cursor-pointer"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* ── Week Navigation & Maximum Days Ahead Controls ── */}
        <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-xs flex flex-col gap-5">
          {/* Centered Week Selector */}
          <div className="flex items-center justify-center gap-3">
            <button
              type="button"
              onClick={handlePrevWeek}
              className="p-2 border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-50 hover:border-slate-300 transition-all cursor-pointer shadow-2xs"
              title="Previous Week"
              aria-label="Previous Week"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>

            <span className="text-sm sm:text-base font-extrabold text-slate-800 min-w-[180px] text-center">
              {formatWeekHeader(weekStart)}
            </span>

            <button
              type="button"
              onClick={handleNextWeek}
              className="p-2 border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-50 hover:border-slate-300 transition-all cursor-pointer shadow-2xs"
              title="Next Week"
              aria-label="Next Week"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>

          {/* Maximum Days Ahead Control */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-center gap-2.5 sm:gap-3 text-xs pt-1 border-t border-slate-100">
            <span className="font-bold text-slate-700 whitespace-nowrap">
              Maximum Days Ahead
            </span>
            <div className="flex items-center gap-2.5">
              <input
                type="number"
                min="1"
                max="365"
                value={maxDaysAhead}
                onChange={e => setMaxDaysAhead(Math.max(1, parseInt(e.target.value, 10) || 1))}
                className="w-16 px-3 py-1.5 text-center font-bold text-slate-800 bg-slate-100 rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-rose-500/20 focus:border-rose-500 text-xs"
              />
              <span className="text-slate-500 font-medium">
                Determines how far in advance clients can book
              </span>
            </div>
          </div>
        </div>

        {/* ── Mobile Day Selector Tabs (< sm) ── */}
        <div className="sm:hidden sticky top-0 z-10 -mx-4 px-4 py-2.5 bg-slate-50/90 backdrop-blur-md border-b border-slate-200/80">
          <div className="flex items-center justify-between gap-1 overflow-x-auto no-scrollbar">
            {DAYS_OF_WEEK.map((dayName, idx) => {
              const dayConfig = weeklyHours[idx];
              const isSelected = mobileActiveDayIndex === idx;
              const hasActiveSlots = dayConfig?.enabled && (dayConfig?.slots?.length || 0) > 0;

              return (
                <button
                  key={dayName}
                  type="button"
                  onClick={() => setMobileActiveDayIndex(idx)}
                  className={`flex-1 min-w-[42px] py-2 px-1 rounded-xl text-center flex flex-col items-center gap-0.5 transition-all cursor-pointer ${
                    isSelected
                      ? 'bg-[#7a0b2e] text-white shadow-xs font-bold'
                      : 'bg-white text-slate-700 hover:bg-slate-100 border border-slate-200 font-medium'
                  }`}
                >
                  <span className="text-[11px] font-bold">{DAY_SHORT_LABELS[idx]}</span>
                  <div className="flex items-center justify-center h-2">
                    {hasActiveSlots && (
                      <span className={`w-1.5 h-1.5 rounded-full ${isSelected ? 'bg-white' : 'bg-emerald-500'}`} />
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* ── Loading Skeleton / State ── */}
        {loading ? (
          <div className="py-24 text-center text-xs text-slate-400 font-bold bg-white rounded-2xl border border-slate-200">
            <Loader2 className="w-8 h-8 text-[#7a0b2e] animate-spin mx-auto mb-3" />
            <span>Loading schedule availability...</span>
          </div>
        ) : (
          /* ── Day Cards List ── */
          <div className="flex flex-col gap-5">
            {DAYS_OF_WEEK.map((dayName, dayIndex) => {
              const day = weeklyHours[dayIndex] || {
                day: dayName,
                enabled: false,
                slots: [],
              };

              // On mobile (< sm), only display the currently active day tab
              const isHiddenOnMobile = mobileActiveDayIndex !== dayIndex;

              const activeSlotSet = new Set(day.slots);
              const cardDateStr = formatDayCardDate(weekStart, dayIndex);

              return (
                <div
                  key={dayName}
                  className={`bg-white rounded-2xl border border-slate-200 p-4 sm:p-5 shadow-xs transition-all ${
                    isHiddenOnMobile ? 'hidden sm:block' : 'block'
                  }`}
                >
                  {/* ── Day Header ── */}
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3.5 border-b border-slate-100">
                    <div className="flex items-center gap-2.5">
                      <span className="text-sm sm:text-base font-extrabold text-slate-900">
                        {dayName} ({cardDateStr})
                      </span>
                      {day.enabled ? (
                        <span className="text-[10px] font-extrabold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
                          {day.slots.length} {day.slots.length === 1 ? 'slot' : 'slots'} active
                        </span>
                      ) : (
                        <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-slate-100 text-slate-500 border border-slate-200">
                          Disabled
                        </span>
                      )}
                    </div>

                    {/* Right side: Presets & Day Active Toggle */}
                    <div className="flex items-center justify-between sm:justify-end gap-3 flex-wrap">
                      {/* Presets */}
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => handleApplyPreset(dayIndex, 'standard')}
                          className="px-2.5 py-1 text-[11px] font-bold text-slate-600 bg-slate-100 hover:bg-slate-200 rounded-lg transition-colors cursor-pointer shadow-2xs"
                        >
                          9:00 AM – 5:00 PM
                        </button>
                        <button
                          type="button"
                          onClick={() => handleApplyPreset(dayIndex, 'all-day')}
                          className="px-2.5 py-1 text-[11px] font-bold text-slate-600 bg-slate-100 hover:bg-slate-200 rounded-lg transition-colors cursor-pointer shadow-2xs"
                        >
                          All Day
                        </button>
                        <button
                          type="button"
                          onClick={() => handleApplyPreset(dayIndex, 'clear')}
                          className="px-2.5 py-1 text-[11px] font-bold text-rose-600 bg-rose-50 hover:bg-rose-100 rounded-lg transition-colors cursor-pointer shadow-2xs"
                        >
                          Clear
                        </button>
                      </div>

                      {/* Day Active Toggle Switch (Labeled "Recurring" as in screenshot) */}
                      <div className="flex items-center gap-2 pl-2 border-l border-slate-200">
                        <span className="text-xs font-bold text-slate-600 select-none">
                          Recurring
                        </span>
                        <button
                          type="button"
                          role="switch"
                          aria-checked={day.enabled}
                          onClick={() => handleToggleDay(dayIndex)}
                          className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-rose-500/20 ${
                            day.enabled ? 'bg-[#7a0b2e]' : 'bg-slate-200'
                          }`}
                        >
                          <span
                            aria-hidden="true"
                            className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-md ring-0 transition duration-200 ease-in-out ${
                              day.enabled ? 'translate-x-5' : 'translate-x-0'
                            }`}
                          />
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* ── 30-Minute Time Pills Grid ── */}
                  <div
                    className={`mt-4 grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-6 xl:grid-cols-8 gap-2 transition-opacity ${
                      !day.enabled ? 'opacity-35 pointer-events-none select-none' : ''
                    }`}
                  >
                    {ALL_HALF_HOUR_SLOTS.map(slot => {
                      const isActive = activeSlotSet.has(slot.timeStr);

                      return (
                        <button
                          key={slot.timeStr}
                          type="button"
                          disabled={!day.enabled}
                          onClick={() => handleToggleSlot(dayIndex, slot.timeStr)}
                          className={`py-2 px-1 text-center text-xs rounded-lg transition-all select-none cursor-pointer ${
                            isActive
                              ? 'bg-[#7a0b2e] hover:bg-[#620925] text-white font-bold shadow-xs border border-transparent'
                              : 'bg-slate-100 hover:bg-slate-200/80 text-slate-700 font-semibold border border-slate-200/70'
                          }`}
                          title={`${slot.label} on ${dayName}`}
                        >
                          {slot.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* ── Save Availability Action (Sticky/Floating) ── */}
        <div className="sticky bottom-4 right-0 flex justify-end pt-4 pb-2 z-20 pointer-events-auto">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || loading}
            className="flex items-center gap-2.5 px-6 py-3.5 rounded-xl bg-[#7a0b2e] hover:bg-[#5f0823] active:bg-[#4d061c] text-white font-extrabold text-sm shadow-xl hover:shadow-2xl transition-all cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed border border-rose-900/20"
          >
            {saving ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Saving Availability...</span>
              </>
            ) : (
              <>
                <Check className="w-4 h-4 stroke-[3]" />
                <span>Save Availability</span>
              </>
            )}
          </button>
        </div>

      </div>
    </div>
  );
}
