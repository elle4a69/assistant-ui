import { Component, lazy, Suspense, useState, useEffect, type ErrorInfo, type FormEvent, type ReactNode } from 'react'
import SmsTriageDashboard from './SmsTriageDashboard'
import SmsClientView from './SmsClientView'
import SettingsView from './SettingsView'
import CustomerBookingView from './CustomerBookingView'
import BookingsView from './BookingsView'
import MobileInboxView from './MobileInboxView'
import BootcampView from './BootcampView'
import ArrivalClientView from './ArrivalClientView'
import ArrivalProviderView from './ArrivalProviderView'
import { getAdminAuthStatus, listArrivalSessions, listBookings, listThreads, loginAdmin, logoutAdmin, type ArrivalSession, type CalendarBooking } from './api'
import { mergeArrivalAlertQueue, playBookingAlarm, processArrivalSessionSnapshot, processArrivalThreadSnapshot, processBookingSnapshot, rememberDismissedBooking, stopIncomingAlarm, unlockIncomingAlarmAudio } from './incomingMessageAlarm'
import { UserCheck, Smartphone, Settings, Calendar, MessagesSquare, CalendarCheck, Bot, DoorOpen, LogOut, LockKeyhole, BellRing, SquareTerminal } from 'lucide-react'

const AgentConsole = lazy(() => import('./AgentConsole'))

class BootcampErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Boot Camp render failure', error, info);
  }

  render() {
    if (this.state.failed) {
      return <div className="flex h-full flex-col items-center justify-center gap-4 bg-slate-950 p-6 text-center text-white"><h2 className="text-lg font-bold">Boot Camp needs to reload</h2><p className="text-sm text-slate-400">Your completed conversations are saved.</p><button onClick={() => window.location.reload()} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-bold">Reload conversations</button></div>;
    }
    return this.props.children;
  }
}

function PortalApp({ onLogout }: { onLogout: () => void }) {
  const isEmbeddedBooking = typeof window !== 'undefined' && window.location.pathname.startsWith('/v2');
  const isStandaloneBooking = typeof window !== 'undefined' && (window.location.pathname === '/booking' || isEmbeddedBooking);
  const isStandaloneArrival = typeof window !== 'undefined' && window.location.pathname === '/arrival';
  const initialPath = typeof window !== 'undefined' ? window.location.pathname : '/';

  const getThreadIdFromUrl = () => {
    if (typeof window === 'undefined') return null;
    const params = new URLSearchParams(window.location.search);
    return params.get('thread');
  };

  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(getThreadIdFromUrl());
  const [newBookingAlert, setNewBookingAlert] = useState<CalendarBooking | null>(null);
  const [customerArrivalAlerts, setCustomerArrivalAlerts] = useState<ArrivalSession[]>([]);

  const [view, setView] = useState<'agent' | 'runner' | 'customer' | 'settings' | 'booking' | 'bookings' | 'chat' | 'bootcamp' | 'arrival' | 'arrivals'>(
    initialPath === '/arrival' ? 'arrival'
      : initialPath === '/booking' || initialPath.startsWith('/v2') ? 'booking'
      : initialPath === '/bookings' ? 'bookings'
      : initialPath === '/arrivals' ? 'arrivals'
      : initialPath === '/chat' ? 'chat'
      : initialPath === '/bootcamp' ? 'bootcamp'
      : initialPath === '/agent-console' ? 'runner'
      : initialPath === '/sim' ? 'customer'
      : initialPath === '/settings' ? 'settings'
      : 'agent'
  )

  // Listen to browser history navigation (back/forward buttons)
  useEffect(() => {
    const handlePopState = () => {
      const path = window.location.pathname;
      const params = new URLSearchParams(window.location.search);
      setSelectedThreadId(params.get('thread'));
      
      if (path === '/arrival') setView('arrival');
      else if (path === '/booking' || path.startsWith('/v2')) setView('booking');
      else if (path === '/bookings') setView('bookings');
      else if (path === '/arrivals') setView('arrivals');
      else if (path === '/chat') setView('chat');
      else if (path === '/bootcamp') setView('bootcamp');
      else if (path === '/agent-console') setView('runner');
      else if (path === '/sim') setView('customer');
      else if (path === '/settings') setView('settings');
      else setView('agent');
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  useEffect(() => {
    if (isStandaloneBooking || isStandaloneArrival) return;

    let active = true;
    const unlockAudio = () => {
      void unlockIncomingAlarmAudio().catch(() => {
        // Browsers can still decline audio until a later user interaction.
      });
    };
    window.addEventListener('pointerdown', unlockAudio, { once: true });
    window.addEventListener('keydown', unlockAudio, { once: true });

    const pollOnce = async () => {
      try {
        const [threads, arrivalSessions, bookings] = await Promise.all([
          listThreads(),
          listArrivalSessions(),
          listBookings(),
        ]);
        processArrivalThreadSnapshot(threads);
        const dueArrivals = processArrivalSessionSnapshot(arrivalSessions);
        setCustomerArrivalAlerts(current => (
          mergeArrivalAlertQueue(current, arrivalSessions, dueArrivals)
        ));
        const newBookings = processBookingSnapshot(bookings);
        if (newBookings.length > 0) {
          const newestBooking = [...newBookings].sort(
            (left, right) => new Date(right.startTime).getTime() - new Date(left.startTime).getTime(),
          )[0];
          setNewBookingAlert(newestBooking);
          void playBookingAlarm().catch((error) => {
            console.warn('New booking sound was blocked by the browser:', error);
          });
        }
      } catch (error) {
        console.warn('Customer arrival alarm check failed:', error);
      }
    };

    const pollForIncomingMessages = async () => {
      while (active) {
        const locks = navigator.locks;
        if (locks) {
          await locks.request(
            'assistant-ui-incoming-alarm-watcher',
            { mode: 'exclusive', ifAvailable: true },
            async lock => { if (lock) await pollOnce(); },
          );
        } else {
          await pollOnce();
        }
        await new Promise((resolve) => window.setTimeout(resolve, 6000));
      }
    };

    void pollForIncomingMessages();

    return () => {
      active = false;
      window.removeEventListener('pointerdown', unlockAudio);
      window.removeEventListener('keydown', unlockAudio);
    };
  }, [isStandaloneBooking, isStandaloneArrival]);

  useEffect(() => {
    document.documentElement.classList.toggle('booking-embed', isEmbeddedBooking);
    return () => document.documentElement.classList.remove('booking-embed');
  }, [isEmbeddedBooking]);

  const navigateTo = (nextView: 'agent' | 'runner' | 'customer' | 'settings' | 'booking' | 'bookings' | 'chat' | 'bootcamp' | 'arrivals', urlPath: string) => {
    setSelectedThreadId(null);
    setView(nextView);
    window.history.pushState(null, '', urlPath);
  };

  const selectThread = (threadId: string | null) => {
    setSelectedThreadId(threadId);
    setView('chat');
    if (threadId) {
      window.history.pushState(null, '', `/chat?thread=${threadId}`);
    } else {
      window.history.pushState(null, '', '/chat');
    }
  };

  // Only the public booking widget page is strictly standalone (no portal headers/navigation)
  const isStandalone = isStandaloneBooking || isStandaloneArrival;

  const dismissBookingAlert = () => {
    stopIncomingAlarm();
    if (newBookingAlert) rememberDismissedBooking(newBookingAlert);
    setNewBookingAlert(null);
  };

  const openNewBooking = () => {
    dismissBookingAlert();
    navigateTo('bookings', '/bookings');
  };

  const customerArrivalAlert = customerArrivalAlerts[0] || null;

  const dismissArrivalAlert = () => {
    setCustomerArrivalAlerts(current => {
      const remaining = current.slice(1);
      if (remaining.length === 0) stopIncomingAlarm();
      return remaining;
    });
  };

  const openArrival = () => {
    const arrival = customerArrivalAlert;
    setCustomerArrivalAlerts(current => current.filter(item => item.id !== arrival?.id));
    if (!arrival?.threadId) {
      navigateTo('arrivals', '/arrivals');
      return;
    }
    setSelectedThreadId(arrival.threadId);
    setView('chat');
    window.history.pushState(
      null,
      '',
      `/chat?thread=${encodeURIComponent(arrival.threadId)}&arrival=${encodeURIComponent(arrival.id)}`,
    );
  };

  return (
    <div className={`flex w-full flex-col bg-slate-900 ${isEmbeddedBooking ? 'min-h-0 overflow-visible' : 'h-[100dvh] overflow-hidden'}`}>
      
      {/* Top Portal Header (Desktop Only) */}
      {!isStandalone && (
        <header className="hidden sm:flex bg-slate-900 text-white px-4 py-2.5 justify-between items-center gap-3 shadow-md select-none z-30 shrink-0">
          <div className="flex items-center gap-2">
            <div className="bg-indigo-600 p-1.5 rounded-lg shrink-0">
              <Smartphone className="w-4 h-4" />
            </div>
            <span className="font-extrabold text-xs sm:text-sm tracking-wider uppercase whitespace-nowrap">SMS Triage Portal</span>
          </div>

          {/* View Switcher Tabs (Desktop Version) */}
          <div className="flex bg-slate-800 p-1 rounded-lg border border-slate-700 overflow-x-auto max-w-full shrink-0">
            <button
              onClick={() => navigateTo('agent', '/')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[11px] sm:text-xs font-semibold transition-all cursor-pointer border border-transparent whitespace-nowrap ${
                view === 'agent'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <UserCheck className="w-3.5 h-3.5" />
              Agent Console
            </button>
            <button
              onClick={() => navigateTo('runner', '/agent-console')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[11px] sm:text-xs font-semibold transition-all cursor-pointer border border-transparent whitespace-nowrap ${
                view === 'runner'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <SquareTerminal className="w-3.5 h-3.5" />
              Coding Agent
            </button>
            <button
              onClick={() => navigateTo('chat', '/chat')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[11px] sm:text-xs font-semibold transition-all cursor-pointer border border-transparent whitespace-nowrap ${
                view === 'chat'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <MessagesSquare className="w-3.5 h-3.5" />
              Messages
            </button>
            <button
              onClick={() => navigateTo('customer', '/sim')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[11px] sm:text-xs font-semibold transition-all cursor-pointer border border-transparent whitespace-nowrap ${
                view === 'customer'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <Smartphone className="w-3.5 h-3.5" />
              SMS Sim
            </button>
            <button
              onClick={() => navigateTo('bootcamp', '/bootcamp')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[11px] sm:text-xs font-semibold transition-all cursor-pointer border border-transparent whitespace-nowrap ${
                view === 'bootcamp'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <Bot className="w-3.5 h-3.5" />
              Boot Camp
            </button>
            <button
              onClick={() => navigateTo('arrivals', '/arrivals')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[11px] sm:text-xs font-semibold transition-all cursor-pointer border border-transparent whitespace-nowrap ${
                view === 'arrivals' ? 'bg-emerald-600 text-white shadow' : 'text-slate-400 hover:text-white'
              }`}
            >
              <DoorOpen className="w-3.5 h-3.5" />
              Arrivals
            </button>
            <button
              onClick={() => navigateTo('bookings', '/bookings')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[11px] sm:text-xs font-semibold transition-all cursor-pointer border border-transparent whitespace-nowrap ${
                view === 'bookings'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <CalendarCheck className="w-3.5 h-3.5" />
              Bookings
            </button>
            <button
              onClick={() => navigateTo('booking', '/booking')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[11px] sm:text-xs font-semibold transition-all cursor-pointer border border-transparent whitespace-nowrap ${
                view === 'booking'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <Calendar className="w-3.5 h-3.5" />
              Booking Form
            </button>
            <button
              onClick={() => navigateTo('settings', '/settings')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[11px] sm:text-xs font-semibold transition-all cursor-pointer border border-transparent whitespace-nowrap ${
                view === 'settings'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <Settings className="w-3.5 h-3.5" />
              System Settings
            </button>
          </div>
          <button onClick={onLogout} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-2.5 py-2 text-[11px] font-bold text-slate-300 hover:bg-slate-800 hover:text-white" title="Log out">
            <LogOut className="h-3.5 w-3.5" /> Log out
          </button>
        </header>
      )}

      {/* View content shares the viewport with the mobile nav, so no duplicate spacer is needed. */}
      <main className={`${isEmbeddedBooking ? 'flex flex-col overflow-visible' : 'min-h-0 flex-1 flex flex-col overflow-hidden'}`}>
        {view === 'agent' && <SmsTriageDashboard />}
        {view === 'runner' && (
          <Suspense fallback={<div className="flex flex-1 items-center justify-center bg-slate-950 text-sm font-bold text-slate-400">Loading Coding Agent…</div>}>
            <AgentConsole />
          </Suspense>
        )}
        {view === 'customer' && <SmsClientView />}
        {view === 'settings' && <SettingsView />}
        {view === 'booking' && <CustomerBookingView />}
        {view === 'bookings' && <BookingsView onOpenThread={selectThread} />}
        {view === 'chat' && <MobileInboxView selectedId={selectedThreadId} setSelectedId={selectThread} />}
        {view === 'bootcamp' && <BootcampErrorBoundary><BootcampView /></BootcampErrorBoundary>}
        {view === 'arrival' && <ArrivalClientView />}
        {view === 'arrivals' && <ArrivalProviderView />}
      </main>

      {/* Mobile Bottom Navigation Bar (the final row of the full-height app) */}
      {!isStandalone && (
        <nav data-testid="mobile-bottom-nav" className="flex h-[calc(4rem+env(safe-area-inset-bottom))] w-full shrink-0 border-t border-slate-800 bg-slate-900 pb-[env(safe-area-inset-bottom)] text-white shadow-lg sm:hidden z-40 select-none">
          <div className="flex w-full items-center justify-around px-1 overflow-x-auto">
            {[
              { id: 'agent', label: 'Console', icon: <UserCheck className="w-4.5 h-4.5" />, action: () => navigateTo('agent', '/') },
              { id: 'runner', label: 'Agent', icon: <SquareTerminal className="w-4.5 h-4.5" />, action: () => navigateTo('runner', '/agent-console') },
              { id: 'chat', label: 'Messages', icon: <MessagesSquare className="w-4.5 h-4.5" />, action: () => navigateTo('chat', '/chat') },
              { id: 'customer', label: 'SMS Sim', icon: <Smartphone className="w-4.5 h-4.5" />, action: () => navigateTo('customer', '/sim') },
              { id: 'bootcamp', label: 'Camp', icon: <Bot className="w-4.5 h-4.5" />, action: () => navigateTo('bootcamp', '/bootcamp') },
              { id: 'bookings', label: 'Bookings', icon: <CalendarCheck className="w-4.5 h-4.5" />, action: () => navigateTo('bookings', '/bookings') },
              { id: 'arrivals', label: 'Arrivals', icon: <DoorOpen className="w-4.5 h-4.5" />, action: () => navigateTo('arrivals', '/arrivals') },
              { id: 'booking', label: 'Form', icon: <Calendar className="w-4.5 h-4.5" />, action: () => navigateTo('booking', '/booking') },
              { id: 'settings', label: 'Settings', icon: <Settings className="w-4.5 h-4.5" />, action: () => navigateTo('settings', '/settings') },
              { id: 'logout', label: 'Log out', icon: <LogOut className="w-4.5 h-4.5" />, action: onLogout },
            ].map((tab) => {
              const active = view === tab.id;
              return (
                <button
                  key={tab.id}
                  onClick={tab.action}
                  className={`flex flex-col items-center justify-center flex-1 min-w-[54px] py-1 transition-colors cursor-pointer bg-transparent border-none ${
                    active ? 'text-indigo-400 font-bold' : 'text-slate-400'
                  }`}
                >
                  <div className="mb-0.5">{tab.icon}</div>
                  <span className="text-[9.5px] tracking-tight">{tab.label}</span>
                </button>
              );
            })}
          </div>
        </nav>
      )}

      {newBookingAlert && !isStandalone && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/75 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="new-booking-title">
          <div className="w-full max-w-sm overflow-hidden rounded-3xl border-4 border-amber-400 bg-white text-center shadow-2xl">
            <div className="bg-amber-400 px-5 py-5 text-slate-950">
              <BellRing className="mx-auto h-12 w-12 animate-bounce" />
              <h2 id="new-booking-title" className="mt-2 text-2xl font-black uppercase tracking-tight">New Booking</h2>
            </div>
            <div className="space-y-2 px-6 py-5 text-slate-800">
              <p className="text-lg font-black">{newBookingAlert.summary || 'Appointment'}</p>
              <p className="text-sm font-bold">{new Date(newBookingAlert.startTime).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}</p>
              {newBookingAlert.customerPhone && <p className="text-sm text-slate-500">{newBookingAlert.customerPhone}</p>}
            </div>
            <div className="grid grid-cols-2 gap-3 px-5 pb-5">
              <button type="button" onClick={dismissBookingAlert} className="rounded-xl border border-slate-300 px-4 py-3 text-sm font-black text-slate-600">Dismiss</button>
              <button type="button" onClick={openNewBooking} className="rounded-xl bg-indigo-600 px-4 py-3 text-sm font-black text-white">View booking</button>
            </div>
          </div>
        </div>
      )}

      {customerArrivalAlert && !isStandalone && (
        <div className="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="customer-arrival-title">
          <div className="w-full max-w-sm overflow-hidden rounded-3xl border-4 border-emerald-400 bg-white text-center shadow-2xl">
            <div className="bg-emerald-500 px-5 py-5 text-white">
              <DoorOpen className="mx-auto h-12 w-12 animate-pulse" />
              <h2 id="customer-arrival-title" className="mt-2 text-2xl font-black uppercase tracking-tight">Customer Arrived</h2>
            </div>
            <div className="space-y-2 px-6 py-5 text-slate-800">
              <p className="text-lg font-black">{customerArrivalAlert.booking.summary || 'Customer booking'}</p>
              {customerArrivalAlert.booking.customerPhone && <p className="text-sm text-slate-500">{customerArrivalAlert.booking.customerPhone}</p>}
              <p className="text-xs font-black uppercase tracking-wide text-indigo-600">{customerArrivalAlert.smsAccountKey === 'secondary' ? 'Anonymous · Line 2' : 'Tori · Line 1'}</p>
              <p className="text-sm font-bold text-emerald-700">The customer has activated their arrival link.</p>
            </div>
            <div className="grid grid-cols-2 gap-3 px-5 pb-5">
              <button type="button" onClick={dismissArrivalAlert} className="rounded-xl border border-slate-300 px-4 py-3 text-sm font-black text-slate-600">Dismiss</button>
              <button type="button" onClick={openArrival} className="rounded-xl bg-emerald-600 px-4 py-3 text-sm font-black text-white">Open conversation</button>
            </div>
          </div>
        </div>
      )}

    </div>
  )
}

function AdminLogin({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await loginAdmin(username, password);
      onAuthenticated();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-slate-950 p-5 text-slate-900">
      <form onSubmit={submit} className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-6 shadow-2xl">
        <div className="mb-5 flex items-center gap-3">
          <div className="rounded-xl bg-indigo-600 p-2.5 text-white"><LockKeyhole className="h-5 w-5" /></div>
          <div><h1 className="text-lg font-black">Admin login</h1><p className="text-xs text-slate-500">Sign in once on this device.</p></div>
        </div>
        <label className="mb-3 block text-xs font-bold text-slate-600">Username
          <input autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none focus:border-indigo-500" />
        </label>
        <label className="block text-xs font-bold text-slate-600">Password
          <input type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none focus:border-indigo-500" />
        </label>
        {error && <p className="mt-3 rounded-lg bg-rose-50 p-2.5 text-xs font-bold text-rose-700">{error}</p>}
        <button disabled={submitting || !username || !password} className="mt-5 w-full rounded-xl bg-indigo-600 px-4 py-3 text-sm font-black text-white disabled:opacity-50">
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
        <p className="mt-3 text-center text-[10px] leading-4 text-slate-400">Your password is verified by the server and is not saved in the app.</p>
      </form>
    </div>
  );
}

function App() {
  const isPublicView = window.location.pathname === '/booking'
    || window.location.pathname.startsWith('/v2')
    || window.location.pathname === '/arrival';
  const [authenticated, setAuthenticated] = useState<boolean | null>(isPublicView ? true : null);

  useEffect(() => {
    if (isPublicView) return;
    void getAdminAuthStatus()
      .then(result => setAuthenticated(result.authenticated))
      .catch(() => setAuthenticated(false));
  }, [isPublicView]);

  useEffect(() => {
    if (isPublicView) return;
    const requireLogin = () => setAuthenticated(false);
    window.addEventListener('admin-auth-required', requireLogin);
    return () => window.removeEventListener('admin-auth-required', requireLogin);
  }, [isPublicView]);

  if (authenticated === null) {
    return <div className="flex min-h-[100dvh] items-center justify-center bg-slate-950 text-sm font-bold text-slate-400">Checking login…</div>;
  }
  if (!authenticated) return <AdminLogin onAuthenticated={() => setAuthenticated(true)} />;

  const handleLogout = () => {
    void logoutAdmin().finally(() => setAuthenticated(false));
  };
  return <PortalApp onLogout={handleLogout} />;
}

export default App
