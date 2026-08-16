import { useEffect, useMemo, useState } from 'react';
import { BellRing, Download, Loader2, Smartphone } from 'lucide-react';
import { deletePushSubscription, getPushConfig, savePushSubscription } from './api';

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>;
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
  const [installPrompt, setInstallPrompt] = useState<InstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(isStandalone);
  const [pushEnabled, setPushEnabled] = useState(false);
  const [pushAvailable, setPushAvailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const isIos = useMemo(() => /iphone|ipad|ipod/i.test(navigator.userAgent), []);

  useEffect(() => {
    const capturePrompt = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event as InstallPromptEvent);
    };
    const markInstalled = () => { setInstalled(true); setInstallPrompt(null); };
    window.addEventListener('beforeinstallprompt', capturePrompt);
    window.addEventListener('appinstalled', markInstalled);

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
    };
  }, []);

  const install = async () => {
    if (!installPrompt) return;
    await installPrompt.prompt();
    const choice = await installPrompt.userChoice;
    if (choice.outcome === 'accepted') setInstalled(true);
    setInstallPrompt(null);
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
    {!installed && installPrompt && <button onClick={() => void install()} className="flex items-center gap-1.5 rounded-xl border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-bold text-indigo-700"><Download className="h-4 w-4" />Install app</button>}
    {!installed && !installPrompt && isIos && <span className="flex items-center gap-1.5 rounded-xl border border-slate-200 px-3 py-2 text-[11px] font-bold text-slate-600"><Smartphone className="h-4 w-4" />Share → Add to Home Screen</span>}
    {installed && <span className="flex items-center gap-1.5 rounded-xl bg-indigo-50 px-3 py-2 text-[11px] font-bold text-indigo-700"><Smartphone className="h-4 w-4" />App installed</span>}
    <button disabled={!pushAvailable || busy} onClick={() => void togglePush()} title={!pushAvailable ? 'Push alerts are not configured on the server.' : undefined} className={`flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-bold disabled:cursor-not-allowed disabled:opacity-50 ${pushEnabled ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-slate-200 bg-white text-slate-600'}`}>
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <BellRing className="h-4 w-4" />}{pushEnabled ? 'Push alerts on' : 'Enable push alerts'}
    </button>
  </div>;
}
