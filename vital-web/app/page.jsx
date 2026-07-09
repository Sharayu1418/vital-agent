"use client";
/* Orchestrator: sidebar (threads) | chat | live side panel.
 * Server owns conversation state (checkpointer); localStorage owns only the
 * thread list and theme. */
import { useCallback, useEffect, useState } from "react";

import Chat from "./components/Chat";
import Sidebar from "./components/Sidebar";
import SidePanel from "./components/SidePanel";
import { api } from "./lib/api";
import { applyEvent, initialStream, shouldKeepBubble, sseEvents } from "./lib/stream";
import { loadThreads, newThread, renameIfNew, saveThreads, uid } from "./lib/threads";

export default function Home() {
  const [theme, setTheme] = useState("dark");
  const [threads, setThreads] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [pendingPlan, setPendingPlan] = useState(null);
  const [editText, setEditText] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [sleep, setSleep] = useState(null);
  const [events, setEvents] = useState([]);
  const [memories, setMemories] = useState([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);

  // ---- boot: theme + threads + panel data ----
  useEffect(() => {
    const savedTheme = localStorage.getItem("vital_theme") || "dark";
    setTheme(savedTheme);
    document.documentElement.dataset.theme = savedTheme;

    let list = loadThreads(localStorage);
    if (list.length === 0) list = [newThread()];
    setThreads(list);
    setActiveId(list[0].id);
    refreshPanel();
    loadHistory(list[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    localStorage.setItem("vital_theme", next);
  }

  const refreshPanel = useCallback(async () => {
    try {
      const [s, c, m] = await Promise.all([
        api.sleepRecent(), api.calendar(), api.memories(),
      ]);
      if (s.ok) setSleep(await s.json());
      if (c.ok) setEvents((await c.json()).events);
      if (m.ok) setMemories((await m.json()).memories);
    } catch { /* panel is decorative; chat still works */ }
  }, []);

  async function loadHistory(threadId) {
    setMessages([]);
    setPendingPlan(null);
    try {
      const r = await api.threadMessages(threadId);
      if (!r.ok) return;
      const body = await r.json();
      setMessages(body.messages.map((m, i) => ({
        id: `h${i}`, role: m.role === "human" ? "user" : "ai", text: m.text,
      })));
      if (body.pending_approval) setPendingPlan(body.pending_approval.plan);
    } catch { /* fresh thread */ }
  }

  // ---- thread ops ----
  function selectThread(id) {
    if (id === activeId) return;
    setActiveId(id);
    setSidebarOpen(false);
    loadHistory(id);
  }

  function createThread() {
    const t = newThread();
    const list = [t, ...threads];
    setThreads(list);
    saveThreads(list, localStorage);
    setActiveId(t.id);
    setMessages([]);
    setPendingPlan(null);
    setSidebarOpen(false);
  }

  function deleteThread(id) {
    const list = threads.filter((t) => t.id !== id);
    const next = list.length ? list : [newThread()];
    setThreads(next);
    saveThreads(next, localStorage);
    if (id === activeId) {
      setActiveId(next[0].id);
      loadHistory(next[0].id);
    }
  }

  // ---- streaming ----
  async function consume(response) {
    const id = uid();
    let st = initialStream();
    let placed = false;
    const sync = () => {
      if (!placed) {
        placed = true;
        setThinking(false);
        setMessages((m) => [...m, { id, role: "ai", text: st.text, status: st.status }]);
        return;
      }
      setMessages((m) => m.map((msg) =>
        msg.id === id ? { ...msg, text: st.text, status: st.status } : msg));
    };

    for await (const ev of sseEvents(response)) {
      st = applyEvent(st, ev);
      if (ev.event === "approval_required") setPendingPlan(st.plan);
      if (st.text || st.status) sync();
    }
    setThinking(false);
    if (placed) {
      st = { ...st, status: null };
      sync();
      if (!shouldKeepBubble(st)) setMessages((m) => m.filter((msg) => msg.id !== id));
    }
  }

  async function send(text) {
    if (!text.trim() || busy || !activeId) return;
    setBusy(true);
    setThinking(true);
    setInput("");
    setPendingPlan(null);
    setMessages((m) => [...m, { id: uid(), role: "user", text }]);
    const renamed = renameIfNew(threads, activeId, text);
    setThreads(renamed);
    saveThreads(renamed, localStorage);
    try {
      const r = await api.chat(text, activeId);
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({}))).detail ?? `Server error (${r.status})`;
        setMessages((m) => [...m, { id: uid(), role: "ai", text: detail }]);
      } else {
        await consume(r);
      }
    } catch {
      setMessages((m) => [...m, { id: uid(), role: "ai",
        text: "Can't reach the backend — is it running?" }]);
    } finally {
      setBusy(false);
      setThinking(false);
      refreshPanel();
    }
  }

  async function decide(action, feedback = "") {
    if (busy) return;
    setBusy(true);
    setThinking(true);
    setPendingPlan(null);
    setEditText("");
    try {
      const r = await api.approve(action, feedback, activeId);
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({}))).detail ?? `Server error (${r.status})`;
        setMessages((m) => [...m, { id: uid(), role: "ai", text: detail }]);
      } else {
        await consume(r);
      }
    } finally {
      setBusy(false);
      setThinking(false);
      refreshPanel();
    }
  }

  async function upload(file) {
    const r = await api.upload(file).catch(() => null);
    if (!r) {
      setMessages((m) => [...m, { id: uid(), role: "ai",
        text: "Upload failed — can't reach the backend." }]);
      return;
    }
    const body = await r.json().catch(() => ({}));
    setMessages((m) => [...m, {
      id: uid(), role: "ai",
      text: r.ok
        ? `Imported **${body.nights_imported} nights** of sleep data (${body.date_range[0]} → ${body.date_range[1]}). Ask me anything about your sleep.`
        : `Upload failed: ${body.detail ?? r.status}`,
    }]);
    refreshPanel();
  }

  async function forget(key) {
    await api.forget(key);
    setMemories((mems) => mems.filter((m) => m.key !== key));
  }

  async function rate(idx, rating) {
    setMessages((m) => m.map((msg, i) => (i === idx ? { ...msg, rated: rating } : msg)));
    api.feedback(rating, activeId);
  }

  // Human-centric nudge: one gentle, data-aware pull toward the next action
  function nudgeFor() {
    if (!sleep?.nights?.length) {
      return { text: "Tip: upload your sleep data (⬆ top right) and VITAL can analyze your real nights" };
    }
    const target = sleep.target_min ?? 480;
    const debtH = sleep.nights
      .reduce((a, n) => a + Math.max(0, target - n.duration_min), 0) / 60;
    if (debtH >= 2) {
      return { text: `You're ${debtH.toFixed(1)}h behind on sleep this week — ask what to do about it`,
               prompt: "What should I do about my sleep debt this week?" };
    }
    if (!events?.length) {
      return { text: "Nothing on your plan yet — want a weekend built around your energy?",
               prompt: "Plan my weekend around my energy levels" };
    }
    return null;
  }

  return (
    <div className="app">
      <Sidebar threads={threads} activeId={activeId}
        onSelect={selectThread} onNew={createThread} onDelete={deleteThread}
        theme={theme} onToggleTheme={toggleTheme}
        open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <main className="main">
        <header className="topbar">
          <button className="icon-btn only-mobile" onClick={() => setSidebarOpen(true)}>☰</button>
          <span className="topbar-title">
            {threads.find((t) => t.id === activeId)?.title ?? "VITAL"}
          </span>
          <div className="topbar-actions">
            <label className="icon-btn" title="Upload sleep data (CSV or Apple Health XML)">
              ⬆
              <input type="file" accept=".csv,.xml" hidden
                onChange={(e) => e.target.files[0] && upload(e.target.files[0])} />
            </label>
            <button className="icon-btn only-mobile" onClick={() => setPanelOpen(true)}>☾</button>
          </div>
        </header>

        <Chat messages={messages} pendingPlan={pendingPlan} busy={busy}
          thinking={thinking} input={input} setInput={setInput}
          editText={editText} setEditText={setEditText}
          onSend={send} onDecide={decide} onRate={rate} nudge={nudgeFor()} />
      </main>

      <SidePanel sleep={sleep} events={events} memories={memories}
        onForget={forget} open={panelOpen} onClose={() => setPanelOpen(false)} />
    </div>
  );
}
