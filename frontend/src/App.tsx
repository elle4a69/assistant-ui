import { Component, useState, useEffect, type ErrorInfo, type ReactNode } from 'react'
import SmsTriageDashboard from './SmsTriageDashboard'
import SmsClientView from './SmsClientView'
import SettingsView from './SettingsView'
import CustomerBookingView from './CustomerBookingView'
import BookingsView from './BookingsView'
import MobileInboxView from './MobileInboxView'
import BootcampView from './BootcampView'
import ArrivalClientView from './ArrivalClientView'
import ArrivalProviderView from './ArrivalProviderView'
import { listThreads } from './api'
import { processArrivalThreadSnapshot, unlockIncomingAlarmAudio } from './incomingMessageAlarm'
import { UserCheck, Smartphone, Settings, Calendar, MessagesSquare, CalendarCheck, Bot, DoorOpen } from 'lucide-react'

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

function App() {
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

  const [view, setView] = useState<'agent' | 'customer' | 'settings' | 'booking' | 'bookings' | 'chat' | 'bootcamp' | 'arrival' | 'arrivals'>(
    initialPath === '/arrival' ? 'arrival'
      : initialPath === '/booking' || initialPath.startsWith('/v2') ? 'booking'
      : initialPath === '/bookings' ? 'bookings'
      : initialPath === '/arrivals' ? 'arrivals'
      : initialPath === '/chat' ? 'chat'
      : initialPath === '/bootcamp' ? 'bootcamp'
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

    const pollForIncomingMessages = async () => {
      while (active) {
        try {
          processArrivalThreadSnapshot(await listThreads());
        } catch (error) {
          console.warn('Customer arrival alarm check failed:', error);
        }
        await new Promise((resolve) => window.setTimeout(resolve, 6000));
      }
    };

    const locks = navigator.locks;
    if (locks) {
      void locks.request('assistant-ui-incoming-alarm-watcher', { mode: 'exclusive' }, pollForIncomingMessages);
    } else {
      void pollForIncomingMessages();
    }

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

  const navigateTo = (nextView: 'agent' | 'customer' | 'settings' | 'booking' | 'bookings' | 'chat' | 'bootcamp' | 'arrivals', urlPath: string) => {
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
        </header>
      )}

      {/* View Content (Adding bottom padding on mobile to account for sticky nav bar) */}
      <main className={`${isEmbeddedBooking ? 'flex flex-col overflow-visible' : 'flex-1 flex flex-col overflow-hidden pb-16 sm:pb-0'}`}>
        {view === 'agent' && <SmsTriageDashboard />}
        {view === 'customer' && <SmsClientView />}
        {view === 'settings' && <SettingsView />}
        {view === 'booking' && <CustomerBookingView />}
        {view === 'bookings' && <BookingsView onOpenThread={selectThread} />}
        {view === 'chat' && <MobileInboxView selectedId={selectedThreadId} setSelectedId={selectThread} />}
        {view === 'bootcamp' && <BootcampErrorBoundary><BootcampView /></BootcampErrorBoundary>}
        {view === 'arrival' && <ArrivalClientView />}
        {view === 'arrivals' && <ArrivalProviderView />}
      </main>

      {/* Mobile Bottom Navigation Bar (Fixed at very bottom of viewport) */}
      {!isStandalone && (
        <nav className="flex sm:hidden fixed bottom-0 left-0 right-0 bg-slate-900 border-t border-slate-800 text-white z-40 select-none shrink-0 h-16 shadow-lg">
          <div className="flex w-full items-center justify-around px-1 overflow-x-auto">
            {[
              { id: 'agent', label: 'Console', icon: <UserCheck className="w-4.5 h-4.5" />, action: () => navigateTo('agent', '/') },
              { id: 'chat', label: 'Messages', icon: <MessagesSquare className="w-4.5 h-4.5" />, action: () => navigateTo('chat', '/chat') },
              { id: 'customer', label: 'SMS Sim', icon: <Smartphone className="w-4.5 h-4.5" />, action: () => navigateTo('customer', '/sim') },
              { id: 'bootcamp', label: 'Camp', icon: <Bot className="w-4.5 h-4.5" />, action: () => navigateTo('bootcamp', '/bootcamp') },
              { id: 'bookings', label: 'Bookings', icon: <CalendarCheck className="w-4.5 h-4.5" />, action: () => navigateTo('bookings', '/bookings') },
              { id: 'arrivals', label: 'Arrivals', icon: <DoorOpen className="w-4.5 h-4.5" />, action: () => navigateTo('arrivals', '/arrivals') },
              { id: 'booking', label: 'Form', icon: <Calendar className="w-4.5 h-4.5" />, action: () => navigateTo('booking', '/booking') },
              { id: 'settings', label: 'Settings', icon: <Settings className="w-4.5 h-4.5" />, action: () => navigateTo('settings', '/settings') },
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

    </div>
  )
}

export default App
