"use client";
/* VITAL frontend — one client component, on purpose (single-file artifact
 * discipline until real complexity demands splitting).
 *
 * Talks to the FastAPI backend over SSE. Identity is the httponly session
 * cookie the backend issues — hence credentials:"include" on every fetch.
 * SSE events handled: token, status, message, approval_required, done.
 */
import { useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE;

const STARTERS = [
  "I slept 4 hours and I'm somehow buzzing with energy",
  "Bored out of my mind — what should I do this weekend?",
  "I want a hobby but have no idea what",
  "Find me people who are into bouldering",
];

async function* sseEvents(response) {
  // fetch()-based SSE parser (EventSource can't POST or send cookies+body)
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split(/\r?\n\r?\n/);
    buf = frames.pop() ?? "";
    for (const frame of frames) {
      let event = "message", data = [];
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      yield { event, data: data.join("\n") };
    }
  }
}

export default function Home() {
  const [messages, setMessages] = useState([]); // {role, text, status?}
  const [pendingPlan, setPendingPlan] = useState(null);
  const [editText, setEditText] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [memories, setMemories] = useState(null); // null = drawer closed
  const [threadId] = useState("web");
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingPlan]);

  async function consume(response) {
    let aiText = "";
    setMessages((m) => [...m, { role: "ai", text: "" }]);
    for await (const { event, data } of sseEvents(response)) {
      if (event === "token") {
        aiText += data;
        setMessages((m) => [...m.slice(0, -1), { role: "ai", text: aiText }]);
      } else if (event === "message") {
        aiText = data;
        setMessages((m) => [...m.slice(0, -1), { role: "ai", text: aiText }]);
      } else if (event === "status") {
        setMessages((m) => [...m.slice(0, -1),
          { role: "ai", text: aiText, status: data }]);
      } else if (event === "approval_required") {
        setPendingPlan(JSON.parse(data).plan);
      }
    }
    if (!aiText) setMessages((m) => (m[m.length - 1]?.text === "" ? m.slice(0, -1) : m));
  }

  async function send(text) {
    if (!text.trim() || busy) return;
    setBusy(true);
    setInput("");
    setPendingPlan(null);
    setMessages((m) => [...m, { role: "user", text }]);
    try {
      const r = await fetch(`${API}/chat`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, thread_id: threadId }),
      });
      if (r.status === 429) {
        const detail = (await r.json()).detail;
        setMessages((m) => [...m, { role: "ai", text: detail }]);
      } else {
        await consume(r);
      }
    } catch {
      setMessages((m) => [...m, { role: "ai", text: "Can't reach the backend — is it running?" }]);
    } finally {
      setBusy(false);
    }
  }

  async function decide(action, feedback = "") {
    setBusy(true);
    setPendingPlan(null);
    setEditText("");
    try {
      const r = await fetch(`${API}/approve`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, feedback, thread_id: threadId }),
      });
      await consume(r);
    } finally {
      setBusy(false);
    }
  }

  async function upload(file) {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch(`${API}/upload/health`, {
      method: "POST", credentials: "include", body: form,
    });
    const body = await r.json();
    setMessages((m) => [...m, {
      role: "ai",
      text: r.ok
        ? `Imported ${body.nights_imported} nights of sleep data (${body.date_range[0]} → ${body.date_range[1]}). Ask me anything about your sleep.`
        : `Upload failed: ${body.detail}`,
    }]);
  }

  async function toggleMemories() {
    if (memories) return setMemories(null);
    const r = await fetch(`${API}/memories`, { credentials: "include" });
    setMemories((await r.json()).memories);
  }

  async function forget(key) {
    await fetch(`${API}/memories/${key}`, { method: "DELETE", credentials: "include" });
    setMemories((mems) => mems.filter((m) => m.key !== key));
  }

  async function rate(idx, rating) {
    setMessages((m) => m.map((msg, i) => (i === idx ? { ...msg, rated: rating } : msg)));
    await fetch(`${API}/feedback`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating, thread_id: threadId }),
    });
  }

  return (
    <div className="shell">
      <header className="top">
        <h1>VITAL<span>.</span></h1>
        <div className="top-actions">
          <label htmlFor="health-upload"><button onClick={() =>
            document.getElementById("health-upload").click()}>Upload sleep data</button></label>
          <input id="health-upload" type="file" accept=".csv,.xml"
            onChange={(e) => e.target.files[0] && upload(e.target.files[0])} />
          <button onClick={toggleMemories}>
            {memories ? "Hide" : "What VITAL knows"}
          </button>
        </div>
      </header>

      {memories && (
        <div className="drawer">
          <h3>What VITAL knows about you</h3>
          {memories.length === 0 && <p className="hint">Nothing yet — it learns as you chat.</p>}
          {memories.map((m) => (
            <div className="memory-row" key={m.key}>
              <span>{m.fact}</span>
              <button className="ghost-danger" onClick={() => forget(m.key)}>forget</button>
            </div>
          ))}
        </div>
      )}

      {messages.length === 0 && (
        <>
          <p className="hint">One place for sleep, energy, activities, ideas and
            people — agents that know you, instead of search. Try:</p>
          <div className="starters">
            {STARTERS.map((s) => (
              <button key={s} onClick={() => send(s)}>{s}</button>
            ))}
          </div>
        </>
      )}

      {messages.map((m, i) => (
        <div className={`msg ${m.role}`} key={i}>
          <div className="bubble">
            {m.text}
            {m.status && <span className="status-line">{m.status}…</span>}
            {m.role === "ai" && m.text && !busy && (
              <span className="feedback">
                <button className={m.rated === "up" ? "chosen" : ""}
                  onClick={() => rate(i, "up")}>👍</button>
                <button className={m.rated === "down" ? "chosen" : ""}
                  onClick={() => rate(i, "down")}>👎</button>
              </span>
            )}
          </div>
        </div>
      ))}

      {pendingPlan && (
        <div className="plan-card">
          <h3>Proposed plan — your call</h3>
          {pendingPlan.items.map((it, i) => (
            <div className="plan-item" key={i}>
              <span className="when">{it.day} {it.start}–{it.end}</span>
              <span>{it.title}</span>
              <span className="kind">{it.kind}</span>
            </div>
          ))}
          {pendingPlan.tradeoffs && pendingPlan.tradeoffs !== "none" && (
            <p className="plan-tradeoffs">Tradeoffs: {pendingPlan.tradeoffs}</p>
          )}
          <div className="plan-actions">
            <button className="primary" onClick={() => decide("approve")}>Approve</button>
            <button className="ghost-danger" onClick={() => decide("reject")}>Reject</button>
            <input placeholder="…or ask for a change" value={editText}
              onChange={(e) => setEditText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && editText && decide("edit", editText)} />
          </div>
        </div>
      )}

      <div ref={bottomRef} />

      <div className="composer">
        <div className="inner">
          <textarea value={input} placeholder={busy ? "thinking…" : "Talk to VITAL"}
            disabled={busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); }
            }} />
          <button className="primary" disabled={busy} onClick={() => send(input)}>Send</button>
        </div>
      </div>
    </div>
  );
}
