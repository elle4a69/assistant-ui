import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  listThreads,
  getThread,
  sendCustomerSms,
  ThreadListItem,
  Message
} from './api';
import {
  Send,
  Wifi,
  Battery,
  Smartphone,
  MessageCircle,
  Plus
} from 'lucide-react';
import { formatMessageTimestamp } from './messageTimestamp';

export default function SmsClientView() {
  const [customerPhone, setCustomerPhone] = useState('');
  const [activePhone, setActivePhone] = useState<string | null>(null);
  const [threads, setThreads] = useState<ThreadListItem[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [composerText, setComposerText] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const threadListRequestRef = useRef(0);
  const threadMessagesRequestRef = useRef(0);
  const threadIdRef = useRef(threadId);
  threadIdRef.current = threadId;

  // Poll threads to sync thread list and find matching threadId
  const fetchThreadsAndSync = useCallback(async () => {
    const requestId = ++threadListRequestRef.current;
    try {
      const list = await listThreads();
      if (requestId !== threadListRequestRef.current) return;
      setThreads(list);
      
      if (activePhone) {
        const matched = list.find(t => t.customerPhone === activePhone);
        if (matched) {
          setThreadId(matched.id);
        }
      }
    } catch (err) {
      console.error('Failed to poll threads list:', err);
    }
  }, [activePhone]);

  // Poll messages for current thread
  const fetchThreadMessages = useCallback(async () => {
    if (!threadId) return;
    const requestId = ++threadMessagesRequestRef.current;
    try {
      const detail = await getThread(threadId);
      if (requestId !== threadMessagesRequestRef.current || threadIdRef.current !== threadId) return;
      setMessages(detail.messages);
    } catch (err) {
      console.error('Failed to fetch thread messages:', err);
    }
  }, [threadId]);

  // Run polling for threads list
  useEffect(() => {
    let active = true;
    let timeout: number | undefined;
    const poll = async () => {
      await fetchThreadsAndSync();
      if (active) timeout = window.setTimeout(poll, 4000);
    };
    void poll();
    return () => {
      active = false;
      threadMessagesRequestRef.current += 1;
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [fetchThreadsAndSync]);

  // Run polling for messages
  useEffect(() => {
    if (!threadId) {
      setMessages([]);
      return;
    }
    let active = true;
    let timeout: number | undefined;
    const poll = async () => {
      await fetchThreadMessages();
      if (active) timeout = window.setTimeout(poll, 4000);
    };
    void poll();
    return () => {
      active = false;
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [threadId, fetchThreadMessages]);

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleStartChat = (phone: string) => {
    if (!phone.trim()) return;
    const formatted = phone.trim();
    setActivePhone(formatted);
    // Find if thread already exists
    const matched = threads.find(t => t.customerPhone === formatted);
    if (matched) {
      setThreadId(matched.id);
    } else {
      setThreadId(null);
      setMessages([]);
    }
  };

  const handleSend = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!activePhone || !composerText.trim() || loading) return;

    setLoading(true);
    try {
      await sendCustomerSms(activePhone, composerText.trim());
      setComposerText('');
      // Force refresh threads and messages
      await fetchThreadsAndSync();
      if (threadId) {
        await fetchThreadMessages();
      }
    } catch (err) {
      alert('Failed to send SMS');
    } finally {
      setLoading(false);
    }
  };

  const handleQuickSend = async (text: string) => {
    if (!activePhone || loading) return;
    setLoading(true);
    try {
      await sendCustomerSms(activePhone, text);
      // Force refresh threads and messages
      await fetchThreadsAndSync();
      if (threadId) {
        await fetchThreadMessages();
      }
    } catch (err) {
      alert('Failed to send quick SMS');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto bg-slate-50 p-6 font-sans">
      <div className="max-w-4xl mx-auto flex flex-col md:flex-row gap-8 items-start justify-center">
        
        {/* Left Control Column */}
        <div className="w-full md:w-80 shrink-0 flex flex-col gap-4">
          <div className="bg-white border border-slate-200 rounded-2xl shadow-md p-5">
            <h2 className="text-md font-bold text-slate-800 mb-3 flex items-center gap-1.5">
              <Smartphone className="w-4 h-4 text-indigo-600" />
              Simulate Customer Phone
            </h2>
            
            {/* Phone selection form */}
            <div className="flex flex-col gap-3">
              <div>
                <label className="block text-xs font-semibold text-slate-500 mb-1">
                  Select Existing Customer
                </label>
                <select
                  value={activePhone || ''}
                  onChange={(e) => handleStartChat(e.target.value)}
                  className="w-full text-sm bg-slate-50 border border-slate-300 rounded-lg p-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                >
                  <option value="">-- Choose Existing --</option>
                  {threads.map((t) => (
                    <option key={t.id} value={t.customerPhone}>
                      {t.customerPhone} ({t.status})
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-slate-400">OR</span>
                <hr className="flex-1 border-slate-200" />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-500 mb-1">
                  Enter Custom Phone Number
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    placeholder="e.g. +19998887777"
                    value={customerPhone}
                    onChange={(e) => setCustomerPhone(e.target.value)}
                    className="flex-1 text-sm bg-slate-50 border border-slate-300 rounded-lg p-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                  <button
                    onClick={() => {
                      handleStartChat(customerPhone);
                      setCustomerPhone('');
                    }}
                    className="bg-indigo-600 hover:bg-indigo-700 text-white text-xs px-3 rounded-lg font-medium transition-colors flex items-center gap-1 cursor-pointer border border-transparent"
                  >
                    <Plus className="w-3.5 h-3.5" /> Start
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Quick instructions panel */}
          <div className="bg-indigo-50 border border-indigo-100 rounded-2xl p-5 text-xs text-indigo-900 flex flex-col gap-3">
            <h3 className="font-bold text-[13px] flex items-center gap-1.5 text-indigo-950">
              💡 Simulator Quick Guide
            </h3>
            <ol className="list-decimal pl-4 space-y-2 text-slate-700 font-medium leading-relaxed">
              <li>Enter a phone number on the left and click <b>Start</b> (or choose an existing chat).</li>
              <li>Use the two controls in <b>Settings</b> to turn the AI on or choose draft approval mode.</li>
              <li>Type a message like <i>"What services do you have?"</i> or click the <b>📅 Book Appointment</b> chip to test calendar sync!</li>
              <li>Switch tabs to the <b>Agent Console</b> to review incoming logs, take over control manually, or inspect the <b>Calendar agenda</b>.</li>
            </ol>
          </div>
        </div>

        {/* Right Phone Simulator Screen */}
        {activePhone ? (
          /* Smartphone Mockup */
          <div className="w-[360px] h-[640px] border-8 border-slate-800 rounded-[40px] shadow-2xl relative bg-slate-100 flex flex-col overflow-hidden shrink-0">
            {/* Top Speaker/Notch */}
            <div className="absolute top-2 left-1/2 -translate-x-1/2 w-32 h-4 bg-slate-800 rounded-full z-20 flex justify-center items-center">
              <div className="w-12 h-1 bg-slate-600 rounded-full"></div>
            </div>

            {/* Status Bar */}
            <div className="pt-7 px-6 pb-2 bg-slate-800 text-white flex justify-between items-center text-[10px] z-10 select-none">
              <span>12:00 PM</span>
              <div className="flex items-center gap-1">
                <Wifi className="w-3 h-3" />
                <span>5G</span>
                <Battery className="w-3.5 h-3.5 ml-1" />
              </div>
            </div>

            {/* Mobile Screen Header */}
            <div className="bg-slate-800 text-white px-4 py-2 border-t border-slate-700 flex justify-between items-center z-10 shadow-sm">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-full bg-slate-600 flex items-center justify-center text-xs font-bold">
                  SMS
                </div>
                <div className="flex flex-col">
                  <span className="text-xs font-bold truncate max-w-[140px]">{activePhone}</span>
                  <span className="text-[9px] text-emerald-400 font-medium">Online</span>
                </div>
              </div>

              <span className="text-[9px] text-slate-300 font-semibold uppercase tracking-wide">
                AI controlled in Settings
              </span>
            </div>

            {/* Message Area */}
            <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2 bg-slate-100">
              {messages.length === 0 ? (
                <div className="flex-1 flex flex-col justify-center items-center text-slate-400 p-4 text-center">
                  <MessageCircle className="w-12 h-12 mb-2 text-slate-300 stroke-[1.5]" />
                  <p className="text-xs font-medium">No messages yet.</p>
                  <p className="text-[10px] text-slate-400 mt-0.5">Send a text below to initiate this thread on the server.</p>
                </div>
              ) : (
                messages.map((m) => {
                  const isCustomer = m.role === 'customer';
                  if (m.role === 'system') {
                    // Display system message as centered automated text
                    return (
                      <div key={m.id} className="self-center my-1 max-w-[85%] bg-slate-200/80 border border-slate-300 text-[10px] text-slate-500 px-2 py-1 rounded text-center">
                        <div>🤖 Auto-Reply: {m.text}</div>
                        <time dateTime={m.at} className="mt-0.5 block text-[8px] text-slate-400">
                          {m.at ? formatMessageTimestamp(m.at) : ''}
                        </time>
                      </div>
                    );
                  }
                  return (
                    <div
                      key={m.id}
                      className={`max-w-[75%] p-2.5 text-xs shadow-sm ${
                        isCustomer
                          ? 'self-end bg-emerald-600 text-white rounded-l-lg rounded-tr-lg'
                          : 'self-start bg-white text-slate-800 border border-slate-200 rounded-r-lg rounded-tl-lg'
                      }`}
                    >
                      <p className="whitespace-pre-wrap">{m.text}</p>
                      <time dateTime={m.at} className={`block text-[8px] text-right mt-1 ${isCustomer ? 'text-emerald-100' : 'text-slate-400'}`}>
                        {m.at ? formatMessageTimestamp(m.at) : ''}
                      </time>
                    </div>
                  );
                })
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Quick Action Chips */}
            <div className="flex gap-1.5 overflow-x-auto px-3 py-2 bg-slate-50 border-t border-slate-200 select-none scrollbar-none shrink-0">
              <button
                type="button"
                onClick={() => handleQuickSend('book')}
                disabled={loading}
                className="shrink-0 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 text-[10px] px-2.5 py-1 rounded-full font-bold transition-colors cursor-pointer border border-transparent disabled:opacity-50"
              >
                📅 Book Appointment
              </button>
              <button
                type="button"
                onClick={() => handleQuickSend('1')}
                disabled={loading}
                className="shrink-0 bg-slate-100 hover:bg-slate-200 text-slate-700 text-[10px] px-2.5 py-1 rounded-full font-bold transition-colors cursor-pointer border border-transparent disabled:opacity-50"
              >
                1️⃣ Option 1
              </button>
              <button
                type="button"
                onClick={() => handleQuickSend('2')}
                disabled={loading}
                className="shrink-0 bg-slate-100 hover:bg-slate-200 text-slate-700 text-[10px] px-2.5 py-1 rounded-full font-bold transition-colors cursor-pointer border border-transparent disabled:opacity-50"
              >
                2️⃣ Option 2
              </button>
              <button
                type="button"
                onClick={() => handleQuickSend('3')}
                disabled={loading}
                className="shrink-0 bg-slate-100 hover:bg-slate-200 text-slate-700 text-[10px] px-2.5 py-1 rounded-full font-bold transition-colors cursor-pointer border border-transparent disabled:opacity-50"
              >
                3️⃣ Option 3
              </button>
              <button
                type="button"
                onClick={() => handleQuickSend('cancel')}
                disabled={loading}
                className="shrink-0 bg-rose-50 hover:bg-rose-100 text-rose-700 text-[10px] px-2.5 py-1 rounded-full font-bold transition-colors cursor-pointer border border-transparent disabled:opacity-50"
              >
                ❌ Cancel scheduling
              </button>
            </div>

            {/* Composer */}
            <form onSubmit={handleSend} className="p-3 bg-white border-t border-slate-200 flex gap-1.5 items-center">
              <input
                type="text"
                placeholder="Text Message"
                value={composerText}
                onChange={(e) => setComposerText(e.target.value)}
                disabled={loading}
                className="flex-1 bg-slate-100 border border-slate-300 rounded-full px-3 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:bg-slate-50 disabled:text-slate-400"
              />
              <button
                type="submit"
                disabled={loading || !composerText.trim()}
                className="w-8 h-8 rounded-full bg-emerald-600 hover:bg-emerald-700 text-white flex items-center justify-center transition-colors disabled:bg-slate-300 disabled:cursor-not-allowed cursor-pointer border border-transparent"
              >
                <Send className="w-3.5 h-3.5" />
              </button>
            </form>

            {/* Bottom Home Indicator bar */}
            <div className="pb-2 bg-white flex justify-center z-10 select-none">
              <div className="w-28 h-1 bg-slate-300 rounded-full"></div>
            </div>
          </div>
        ) : (
          /* Empty state device placeholder */
          <div className="w-[360px] h-[640px] border-4 border-dashed border-slate-300 bg-slate-50/50 rounded-[40px] flex flex-col justify-center items-center text-slate-400 p-8 text-center shrink-0 shadow-sm">
            <Smartphone className="w-16 h-16 text-slate-350 stroke-[1.5] mb-3" />
            <h3 className="text-sm font-bold text-slate-650">No Device Screen Active</h3>
            <p className="text-[11px] text-slate-400 mt-1 max-w-[240px] leading-relaxed">Choose an existing customer or enter a number on the left control panel to power on the phone simulator.</p>
          </div>
        )}

      </div>
    </div>
  );
}
