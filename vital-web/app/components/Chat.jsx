"use client";
import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";

// react-markdown is safe by default (no raw HTML). Links open in a new tab.
const mdComponents = {
  a: (props) => <a {...props} target="_blank" rel="noopener noreferrer" />,
};

const STARTERS = [
  { icon: "🌙", text: "I slept 4 hours and I'm somehow buzzing with energy" },
  { icon: "🧭", text: "Bored out of my mind — what should I do this weekend?" },
  { icon: "💡", text: "I want a hobby but have no idea what" },
  { icon: "🤝", text: "Find me people who are into bouldering" },
];

function Hero({ onStarter }) {
  return (
    <div className="hero">
      <h2 className="hero-title">Where should your <span>energy</span> go today?</h2>
      <p className="hero-sub">
        Sleep, activities, ideas and people — one conversation with agents
        that know you, instead of twelve tabs of search.
      </p>
      <div className="starter-grid">
        {STARTERS.map((s) => (
          <button className="starter-card" key={s.text} onClick={() => onStarter(s.text)}>
            <span className="starter-icon">{s.icon}</span>
            <span>{s.text}</span>
          </button>
        ))}
      </div>
      <p className="hero-foot">
        Nothing is committed to your calendar without your approval. Everything
        VITAL learns about you stays visible and deletable.
      </p>
    </div>
  );
}

function PlanCard({ plan, editText, setEditText, onDecide, busy }) {
  return (
    <div className="plan-card">
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
  onSend, onDecide, onRate,
}) {
  const bottomRef = useRef(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingPlan, thinking]);

  return (
    <div className="chat">
      <div className="chat-scroll">
        <div className="chat-col">
          {messages.length === 0 && !busy && <Hero onStarter={onSend} />}

          {messages.map((m, i) => (m.role === "ai" && !m.text && !m.status) ? null : (
            <div className={`msg ${m.role}`} key={m.id ?? i}>
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
            <div className="msg ai">
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

      <div className="composer">
        <div className="composer-inner">
          <textarea value={input} rows={1}
            placeholder={busy ? "thinking…" : "Talk to VITAL"}
            disabled={busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(input); }
            }} />
          <button className="primary send" disabled={busy || !input.trim()}
            onClick={() => onSend(input)}>↑</button>
        </div>
        <p className="composer-hint">VITAL can make mistakes — plans always wait for your approval.</p>
      </div>
    </div>
  );
}
