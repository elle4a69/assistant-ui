import { useEffect, useMemo, useState } from 'react';
import { BellRing, Download, Loader2, MoreVertical, Share2, Smartphone, X } from 'lucide-react';
import { deletePushSubscription, getPushConfig, savePushSubscription } from './api';

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>;
}

declare global {
  interface Window {
    __toriInstallPrompt?: InstallPromptEvent | null;
  }
}

function base64UrlBytes(value: string): ArrayBuffer {
  const padding = '='.repeat((4 - value.length % 4) % 4);
  const decoded = window.atob((value + padding).replace(/-/g, '+').replace(/_/g, '/'));
  const bytes = new Uint8Array(decoded.length);
  for (let index = 0; index < decoded.length; index += 1) bytes[index] = decoded.charCodeAt(index);
  return bytes.buffer;
}

function isStandalone() {
  return window.matchMedia('(display-mode: standalone)').matches
    || Boolean((navigator as Navigator & { standalone?: boolean }).standalone);
}

export default function PwaControls({ onError }: { onError: (message: string) => void }) {
  const [installPrompt, setInstallPrompt] = useState<InstallPromptEvent | null>(() => window.__toriInstallPrompt || null);
  const [installed, setInstalled] = useState(isStandalone);
  const [showInstallHelp, setShowInstallHelp] = useState(false);
  const [installBusy, setInstallBusy] = useState(false);
  const [pushEnabled, setPushEnabled] = useState(false);
  const [pushAvailable, setPushAvailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const isIos = useMemo(() => /iphone|ipad|ipod/i.test(navigator.userAgent), []);
  const isAndroid = useMemo(() => /android/i.test(navigator.userAgent), []);

  useEffect(() => {
    const capturePrompt = (event: Event) => {
      event.preventDefault();
      const prompt = event as InstallPromptEvent;
      window.__toriInstallPrompt = prompt;
      setInstallPrompt(prompt);
    };
    const useCapturedPrompt = () => setInstallPrompt(window.__toriInstallPrompt || null);
    const markInstalled = () => {
      window.__toriInstallPrompt = null;
      setInstalled(true);
      setInstallPrompt(null);
      setShowInstallHelp(false);
    };
    window.addEventListener('beforeinstallprompt', capturePrompt);
    window.addEventListener('appinstalled', markInstalled);
    window.addEventListener('tori-install-available', useCapturedPrompt);
    window.addEventListener('tori-app-installed', markInstalled);

    void (async () => {
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
      try {
        const [config, registration] = await Promise.all([getPushConfig(), navigator.serviceWorker.ready]);
        setPushAvailable(config.supported && config.configured && Boolean(config.publicKey));
        const existing = await registration.pushManager.getSubscription();
        setPushEnabled(Boolean(existing));
        if (existing && config.configured) await savePushSubscription(existing.toJSON());
      } catch {
        // The normal arrival screen remains usable if push setup is unavailable.
      }
    })();

    return () => {
      window.removeEventListener('beforeinstallprompt', capturePrompt);
      window.removeEventListener('appinstalled', markInstalled);
      window.removeEventListener('tori-install-available', useCapturedPrompt);
      window.removeEventListener('tori-app-installed', markInstalled);
    };
  }, []);

  const install = async () => {
    const prompt = installPrompt || window.__toriInstallPrompt || null;
    if (!prompt) {
      setShowInstallHelp(true);
      return;
    }
    setInstallBusy(true);
    try {
      await prompt.prompt();
      const choice = await prompt.userChoice;
      window.__toriInstallPrompt = null;
      setInstallPrompt(null);
      if (choice.outcome === 'accepted') setInstalled(true);
      else setShowInstallHelp(true);
    } catch {
      window.__toriInstallPrompt = null;
      setInstallPrompt(null);
      setShowInstallHelp(true);
      onError('The browser could not open its installer. Follow the displayed browser steps instead.');
    } finally {
      setInstallBusy(false);
    }
  };

  const togglePush = async () => {
    setBusy(true);
    try {
      const registration = await navigator.serviceWorker.ready;
      const existing = await registration.pushManager.getSubscription();
      if (existing) {
        const snapshot = existing.toJSON();
        await deletePushSubscription(snapshot);
        await existing.unsubscribe();
        setPushEnabled(false);
        return;
      }
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') throw new Error('Notifications were not allowed. Enable them in this site’s browser settings.');
      const config = await getPushConfig();
      if (!config.configured || !config.publicKey) throw new Error('Push alerts are not configured on the server yet.');
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: base64UrlBytes(config.publicKey),
      });
      await savePushSubscription(subscription.toJSON());
      setPushEnabled(true);
    } catch (error) {
      onError(error instanceof Error ? error.message : 'Could not change push alerts.');
    } finally {
      setBusy(false);
    }
  };

  return <div className="flex flex-wrap items-center justify-end gap-2">
    {!installed && <button disabled={installBusy} onClick={() => void install()} className="flex items-center gap-1.5 rounded-xl border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-bold text-indigo-700 disabled:cursor-wait disabled:opacity-60">
      {installBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
      {installPrompt ? 'Install app' : 'How to install'}
    </button>}
    {installed && <span className="flex items-center gap-1.5 rounded-xl bg-indigo-50 px-3 py-2 text-[11px] font-bold text-indigo-700"><Smartphone className="h-4 w-4" />App installed</span>}
    <button disabled={!pushAvailable || busy} onClick={() => void togglePush()} title={!pushAvailable ? 'Push alerts are not configured on the server.' : undefined} className={`flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-bold disabled:cursor-not-allowed disabled:opacity-50 ${pushEnabled ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-slate-200 bg-white text-slate-600'}`}>
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <BellRing className="h-4 w-4" />}{pushEnabled ? 'Push alerts on' : 'Enable push alerts'}
    </button>
    {showInstallHelp && <div className="fixed inset-0 z-[100] flex items-end justify-center bg-slate-950/65 p-3 sm:items-center" role="dialog" aria-modal="true" aria-labelledby="install-app-title">
      <div className="w-full max-w-md rounded-3xl bg-white p-5 text-left shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="install-app-title" className="text-lg font-black text-slate-900">Install Tori Operations</h2>
            <p className="mt-1 text-sm text-slate-600">Your browser controls the final installation step.</p>
          </div>
          <button onClick={() => setShowInstallHelp(false)} aria-label="Close install instructions" className="rounded-full p-2 text-slate-500 hover:bg-slate-100"><X className="h-5 w-5" /></button>
        </div>
        {isIos ? <ol className="mt-5 space-y-4 text-sm text-slate-700">
          <li className="flex gap-3"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-100 font-black text-indigo-700">1</span><span>Open this page in <strong>Safari</strong>.</span></li>
          <li className="flex gap-3"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-100 font-black text-indigo-700">2</span><span className="flex items-center gap-1.5">Tap <Share2 className="h-4 w-4" /> <strong>Share</strong>.</span></li>
          <li className="flex gap-3"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-100 font-black text-indigo-700">3</span><span>Choose <strong>Add to Home Screen</strong>, then tap <strong>Add</strong>.</span></li>
        </ol> : <ol className="mt-5 space-y-4 text-sm text-slate-700">
          <li className="flex gap-3"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-100 font-black text-indigo-700">1</span><span className="flex items-center gap-1.5">Open the browser menu <MoreVertical className="h-4 w-4" />.</span></li>
          <li className="flex gap-3"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-100 font-black text-indigo-700">2</span><span>Choose <strong>{isAndroid ? 'Install app' : 'Install Tori Operations'}</strong> or <strong>Add to Home screen</strong>.</span></li>
          <li className="flex gap-3"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-100 font-black text-indigo-700">3</span><span>Confirm the installation when your browser asks.</span></li>
        </ol>}
        <button onClick={() => setShowInstallHelp(false)} className="mt-6 w-full rounded-xl bg-slate-900 px-4 py-3 text-sm font-black text-white">Got it</button>
      </div>
    </div>}
  </div>;
}
