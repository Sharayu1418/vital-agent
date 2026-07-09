"use client";
import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";

// react-markdown is safe by default (no raw HTML). Links open in a new tab.
const mdComponents = {
  a: (props) => <a {...props} target="_blank" rel="noopener noreferrer" />,
};

const STARTERS = [
  "I slept 4 hours but I'm buzzing with energy",
  "What should I do this weekend?",
  "I want a hobby but don't know what",
  "Find people who are into bouldering",
];

function greeting() {
  const h = new Date().getHours();
  if (h >= 5 && h < 12) return { hi: "Good morning", line: "Where should today's energy go?" };
  if (h < 17) return { hi: "Good afternoon", line: "Time for a reset, an idea, or a plan?" };
  if (h < 22) return { hi: "Good evening", line: "How did today treat you?" };
  return { hi: "Up late?", line: "Let's look after tomorrow-you." };
}

function Composer({ input, setInput, onSend, busy, hero = false }) {
  const ref = useRef(null);
  useEffect(() => { if (hero) ref.current?.focus(); }, [hero]);
  return (
    <div className={`composer-inner ${hero ? "hero-pill" : ""}`}>
      <textarea ref={ref} value={input} rows={1}
        placeholder={busy ? "thinking…" : "Ask anything…"}
        disabled={busy}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(input); }
        }} />
      <button className="send" aria-label="Send" disabled={busy || !input.trim()}
        onClick={() => onSend(input)}>↑</button>
    </div>
  );
}

function Hero({ onStarter, nudge, input, setInput, onSend, busy }) {
  const g = greeting();
  return (
    <div className="hero">
      <p className="hero-hi">{g.hi}</p>
      <h2 className="hero-title">{g.line}</h2>

      <Composer input={input} setInput={setInput} onSend={onSend} busy={busy} hero />

      {nudge && (
        nudge.prompt ? (
          <button className="nudge" onClick={() => onSend(nudge.prompt)}>
            {nudge.text} <span className="nudge-go">→</span>
          </button>
        ) : (
          <p className="nudge nudge-static">{nudge.text}</p>
        )
      )}

      <div className="starter-row">
        {STARTERS.map((s) => (
          <button className="starter-chip" key={s} onClick={() => onStarter(s)}>{s}</button>
        ))}
      </div>

      <p className="hero-foot">
        One place for sleep, energy, activities, ideas and people.
        Nothing touches your calendar without your OK.
      </p>
    </div>
  );
}

function PlanCard({ plan, editText, setEditText, onDecide, busy }) {
  return (
    <div className="plan-card rise">
      <h3>Proposed plan — your call</h3>
      {plan.items.map((it, i) => (
        <div className="plan-item" key={i}>
          <span className="when">{it.day} {it.start}–{it.end}</span>
          <span className="plan-what">{it.title}</span>
          <span className={`kind-chip kind-${it.kind}`}>{it.kind}</span>
        </div>
      ))}
      {plan.tradeoffs && plan.tradeoffs !== "none" && (
        <p className="plan-tradeoffs">Tradeoffs: {plan.tradeoffs}</p>
      )}
      <div className="plan-actions">
        <button className="primary" disabled={busy} onClick={() => onDecide("approve")}>
          Approve
        </button>
        <button className="ghost-danger" disabled={busy} onClick={() => onDecide("reject")}>
          Reject
        </button>
        <input placeholder="…or ask for a change" value={editText}
          onChange={(e) => setEditText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && editText && onDecide("edit", editText)} />
      </div>
    </div>
  );
}

export default function Chat({
  messages, pendingPlan, busy, thinking, input, setInput, editText, setEditText,
  onSend, onDecide, onRate, nudge,
}) {
  const bottomRef = useRef(null);
  const empty = messages.length === 0 && !busy;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingPlan, thinking]);

  return (
    <div className="chat">
      <div className="chat-scroll">
        <div className="chat-col">
          {empty && (
            <Hero onStarter={onSend} nudge={nudge}
              input={input} setInput={setInput} onSend={onSend} busy={busy} />
          )}

          {messages.map((m, i) => (m.role === "ai" && !m.text && !m.status) ? null : (
            <div className={`msg ${m.role} rise`} key={m.id ?? i}>
              <div className="bubble">
                {m.role === "ai"
                  ? <ReactMarkdown components={mdComponents}>{m.text}</ReactMarkdown>
                  : m.text}
                {m.status && <span className="status-line">{m.status}…</span>}
              </div>
              {m.role === "ai" && m.text && !busy && (
                <div className="feedback-row">
                  <button className={m.rated === "up" ? "chosen" : ""}
                    onClick={() => onRate(i, "up")}>👍</button>
                  <button className={m.rated === "down" ? "chosen" : ""}
                    onClick={() => onRate(i, "down")}>👎</button>
                </div>
              )}
            </div>
          ))}

          {thinking && (
            <div className="msg ai rise">
              <div className="bubble thinking"><span /><span /><span /></div>
            </div>
          )}

          {pendingPlan && (
            <PlanCard plan={pendingPlan} editText={editText} setEditText={setEditText}
              onDecide={onDecide} busy={busy} />
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      {!empty && (
        <div className="composer">
          <Composer input={input} setInput={setInput} onSend={onSend} busy={busy} />
          <p className="composer-hint">VITAL can make mistakes — plans always wait for your approval.</p>
        </div>
      )}
    </div>
  );
}
