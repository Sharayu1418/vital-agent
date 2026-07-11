"use client";
/* Orchestrator: sidebar (threads) | chat | live side panel.
 * Server owns conversation state (checkpointer); localStorage owns only the
 * thread list and theme. */
import { useCallback, useEffect, useRef, useState } from "react";

import Chat from "./components/Chat";
import { MenuIcon, MoonIcon, SpeakerIcon, UploadIcon } from "./components/icons";
import Sidebar from "./components/Sidebar";
import SidePanel from "./components/SidePanel";
import { api, setTokenProvider } from "./lib/api";
import {
  clearSessionTransport, idToken, signInWithGoogle, signOutUser, watchAuth,
} from "./lib/auth";
import { createGenerationGuard } from "./lib/guard";
import { isSynthesisSupported } from "./lib/speech";
import { applyEvent, initialStream, shouldKeepBubble, sseEvents } from "./lib/stream";
import { firstNameFrom, themeForHour } from "./lib/theme";
import {
  loadThreads, mergeThreads, newThread, renameIfNew, saveThreads, uid,
} from "./lib/threads";

export default function Home() {
  const [userName, setUserName] = useState(null); // null = not loaded yet
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
  const [ttsAvailable, setTtsAvailable] = useState(false);
  const [autoRead, setAutoRead] = useState(false);
  const [authUser, setAuthUser] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState(null);
  // identity epoch: async work captures a liveness check; sign-out /
  // account change invalidates it so stale responses can't write state
  const guardRef = useRef(createGenerationGuard());

  // ---- boot: theme + prefs + auth subscription ----
  useEffect(() => {
    // theme follows the clock (no toggle): sunrise mornings, night otherwise;
    // re-checked every few minutes so it flips while the tab stays open
    const applyTheme = () =>
      (document.documentElement.dataset.theme = themeForHour(new Date().getHours()));
    applyTheme();
    const themeTimer = setInterval(applyTheme, 5 * 60 * 1000);

    setUserName(localStorage.getItem("vital_name") ?? "");
    setTtsAvailable(isSynthesisSupported());
    setAutoRead(localStorage.getItem("vital_read_aloud") === "1");

    // From here every API call can carry a Firebase ID token (null when
    // signed out → no header). watchAuth wraps onIdTokenChanged, so its
    // FIRST callback is the "initial auth state known" signal that unlocks
    // account-scoped loading below.
    setTokenProvider(idToken);
    let unsubscribe = () => {};
    watchAuth((user) => {
      setAuthUser(user);
      setAuthReady(true);
    }).then((u) => { unsubscribe = u; });
    return () => { clearInterval(themeTimer); unsubscribe(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- threads + panel data: only once the auth state is known, and again
  // ---- on sign-in/out (identity changes what the backend returns) ----
  useEffect(() => {
    if (!authReady) return undefined;
    const live = guardRef.current.begin();  // new identity epoch
    (async () => {
      let list = loadThreads(localStorage);
      if (authUser) {
        try {
          const r = await api.threads();   // this account's server-side index
          if (r.ok && live()) list = mergeThreads(list, (await r.json()).threads);
        } catch { /* offline: the local list still works */ }
      }
      if (!live()) return;  // signed out / switched while we were loading
      if (list.length === 0) list = [newThread()];
      setThreads(list);
      saveThreads(list, localStorage);
      setActiveId(list[0].id);
      setMessages([]);
      setPendingPlan(null);
      refreshPanel();
      loadHistory(list[0].id);
    })();
    return () => guardRef.current.invalidate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authReady, authUser?.uid]);

  // ---- account controls (Sidebar) ----
  async function signIn() {
    setAuthError(null);
    setAuthBusy(true);
    try {
      await signInWithGoogle();  // watchAuth fires → data effect reloads
    } catch (err) {
      setAuthError(err.message);
    } finally {
      setAuthBusy(false);
    }
  }

  async function signOut() {
    setAuthBusy(true);
    // Account data leaves the screen NOW — synchronously, before any
    // network round-trip — and every in-flight load started under this
    // account is invalidated so it can't write state afterwards.
    guardRef.current.invalidate();
    clearSessionTransport(localStorage);
    localStorage.removeItem("vital_threads");  // anon reload starts clean
    const fresh = newThread();
    setThreads([fresh]);
    setActiveId(fresh.id);
    setMessages([]);
    setPendingPlan(null);
    setSleep(null);
    setEvents([]);
    setMemories([]);
    // guarded finally blocks won't run for the old identity, so reset the
    // request flags here or the composer stays stuck "thinking"
    setBusy(false);
    setThinking(false);
    try {
      await signOutUser();  // Firebase; watchAuth fires null → anon reload
      await api.logout();   // expire the HttpOnly session server-side
      // The server keeps all account data — signing back in with the same
      // Google account restores it.
    } finally {
      setAuthBusy(false);
    }
  }

  function toggleAutoRead() {
    const next = !autoRead;
    setAutoRead(next);
    localStorage.setItem("vital_read_aloud", next ? "1" : "0");
  }

  function saveName(raw) {
    const name = firstNameFrom(raw);
    setUserName(name || "-");        // "-" = asked and skipped; never ask again
    localStorage.setItem("vital_name", name || "-");
  }

  const refreshPanel = useCallback(async () => {
    const live = guardRef.current.watch();  // stale after sign-out/switch
    try {
      const [s, c, m] = await Promise.all([
        api.sleepRecent(), api.calendar(), api.memories(),
      ]);
      // parse everything FIRST — body parsing is itself async, so the
      // liveness check must come immediately before the state writes
      const sleepBody = s.ok ? await s.json() : null;
      const eventsBody = c.ok ? (await c.json()).events : null;
      const memoriesBody = m.ok ? (await m.json()).memories : null;
      if (!live()) return;
      if (sleepBody) setSleep(sleepBody);
      if (eventsBody) setEvents(eventsBody);
      if (memoriesBody) setMemories(memoriesBody);
    } catch { /* panel is decorative; chat still works */ }
  }, []);

  async function loadHistory(threadId) {
    const live = guardRef.current.watch();
    setMessages([]);
    setPendingPlan(null);
    try {
      const r = await api.threadMessages(threadId);
      if (!r.ok) return;
      const body = await r.json();
      if (!live()) return;  // identity changed while the history was in flight
      setMessages(body.messages.map((m, i) => ({
        id: `h${i}`, role: m.role === "human" ? "user" : "ai", text: m.text,
        fromHistory: true, // never auto-read replies that were already there
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
    const removed = threads.find((t) => t.id === id);
    const list = threads.filter((t) => t.id !== id);
    const next = list.length ? list : [newThread()];
    setThreads(next);
    saveThreads(next, localStorage);
    if (id === activeId) {
      setActiveId(next[0].id);
      loadHistory(next[0].id);
    }
    // Signed in: unlist server-side too, or the row reappears on the next
    // device sync (sidebar removal only; conversation data is untouched).
    // fetch resolves on 401/500, so check res.ok — a silently failed delete
    // would resurrect the thread later. On failure, restore + explain.
    if (authUser && removed) {
      const live = guardRef.current.watch();
      api.threadDelete(id)
        .then((res) => {
          if (!res.ok) throw new Error(`server returned ${res.status}`);
        })
        .catch(() => {
          if (!live()) return;  // signed out meanwhile: don't leak it back
          setThreads((cur) => {
            if (cur.some((t) => t.id === id)) return cur;
            const restored = [removed, ...cur];
            saveThreads(restored, localStorage);
            return restored;
          });
          setAuthError("Couldn't remove that chat from your account, so it's back in the list. Try again in a moment.");
        });
    }
  }

  // ---- streaming ----
  async function consume(response, live) {
    const id = uid();
    let st = initialStream();
    let placed = false;
    const sync = () => {
      if (!live()) return;  // stream outlived its identity: stop writing
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
      if (!live()) break;  // identity changed: stop consuming, not just writing
      st = applyEvent(st, ev);
      if (ev.event === "approval_required") setPendingPlan(st.plan);
      if (st.text || st.status) sync();
    }
    if (!live()) return;   // busy/thinking now belong to the next identity
    setThinking(false);
    if (placed) {
      st = { ...st, status: null };
      sync();
      if (!shouldKeepBubble(st)) setMessages((m) => m.filter((msg) => msg.id !== id));
    }
  }

  async function send(text) {
    // authReady gate: a message sent before the initial Firebase state is
    // known could land under the wrong identity
    if (!text.trim() || busy || !activeId || !authReady) return;
    setBusy(true);
    setThinking(true);
    setInput("");
    setPendingPlan(null);
    setMessages((m) => [...m, { id: uid(), role: "user", text }]);
    const renamed = renameIfNew(threads, activeId, text);
    setThreads(renamed);
    saveThreads(renamed, localStorage);
    const live = guardRef.current.watch();
    try {
      const r = await api.chat(text, activeId);
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({}))).detail ?? `Server error (${r.status})`;
        if (live()) setMessages((m) => [...m, { id: uid(), role: "ai", text: detail }]);
      } else {
        await consume(r, live);
      }
    } catch {
      if (live()) setMessages((m) => [...m, { id: uid(), role: "ai",
        text: "Can't reach the backend. Is it running?" }]);
    } finally {
      // a stale completion must not flip busy/thinking while the NEXT
      // identity is mid-request; signOut resets these synchronously
      if (live()) {
        setBusy(false);
        setThinking(false);
        refreshPanel();
      }
    }
  }

  async function decide(action, feedback = "") {
    if (busy) return;
    setBusy(true);
    setThinking(true);
    setPendingPlan(null);
    setEditText("");
    const live = guardRef.current.watch();
    try {
      const r = await api.approve(action, feedback, activeId);
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({}))).detail ?? `Server error (${r.status})`;
        if (live()) setMessages((m) => [...m, { id: uid(), role: "ai", text: detail }]);
      } else {
        await consume(r, live);
      }
    } finally {
      if (live()) {
        setBusy(false);
        setThinking(false);
        refreshPanel();
      }
    }
  }

  async function upload(file) {
    const live = guardRef.current.watch();
    const r = await api.upload(file).catch(() => null);
    if (!live()) return;
    if (!r) {
      setMessages((m) => [...m, { id: uid(), role: "ai",
        text: "Upload failed. Can't reach the backend." }]);
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
      return { text: "Tip: upload your sleep data (top right) and VITAL can analyze your real nights" };
    }
    const target = sleep.target_min ?? 480;
    const debtH = sleep.nights
      .reduce((a, n) => a + Math.max(0, target - n.duration_min), 0) / 60;
    if (debtH >= 2) {
      return { text: `You're ${debtH.toFixed(1)}h behind on sleep this week. Ask what to do about it`,
               prompt: "What should I do about my sleep debt this week?" };
    }
    if (!events?.length) {
      return { text: "Nothing on your plan yet. Want a weekend built around your energy?",
               prompt: "Plan my weekend around my energy levels" };
    }
    return null;
  }

  return (
    <div className="app">
      <Sidebar threads={threads} activeId={activeId}
        onSelect={selectThread} onNew={createThread} onDelete={deleteThread}
        open={sidebarOpen} onClose={() => setSidebarOpen(false)}
        authReady={authReady} authUser={authUser} authBusy={authBusy}
        authError={authError} onSignIn={signIn} onSignOut={signOut} />

      <main className="main">
        <header className="topbar">
          <button className="icon-btn only-mobile" aria-label="Open menu"
            onClick={() => setSidebarOpen(true)}><MenuIcon /></button>
          <span className="topbar-title">
            {threads.find((t) => t.id === activeId)?.title ?? "VITAL"}
          </span>
          <div className="topbar-actions">
            {ttsAvailable && (
              <button className={`icon-btn ${autoRead ? "active" : ""}`}
                aria-pressed={autoRead}
                title={autoRead ? "Read replies aloud: on" : "Read replies aloud: off"}
                onClick={toggleAutoRead}><SpeakerIcon /></button>
            )}
            <label className="icon-btn" title="Upload sleep data (CSV or Apple Health XML)">
              <UploadIcon />
              <input type="file" accept=".csv,.xml" hidden
                onChange={(e) => e.target.files[0] && upload(e.target.files[0])} />
            </label>
            <button className="icon-btn only-mobile" aria-label="Open panel"
              onClick={() => setPanelOpen(true)}><MoonIcon /></button>
          </div>
        </header>

        <Chat messages={messages} pendingPlan={pendingPlan} busy={busy}
          thinking={thinking} input={input} setInput={setInput}
          editText={editText} setEditText={setEditText}
          onSend={send} onDecide={decide} onRate={rate} nudge={nudgeFor()}
          autoRead={autoRead} userName={userName} onSaveName={saveName}
          suggestedName={authUser?.displayName ?? ""} />
      </main>

      <SidePanel sleep={sleep} events={events} memories={memories}
        onForget={forget} open={panelOpen} onClose={() => setPanelOpen(false)} />
    </div>
  );
}
