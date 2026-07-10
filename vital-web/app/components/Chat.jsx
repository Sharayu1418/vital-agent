"use client";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import {
  getRecognitionCtor, isSynthesisSupported, joinTranscript,
  recognitionErrorText, stripMarkdownForSpeech,
} from "../lib/speech";
import { dailyLine, firstNameFrom } from "../lib/theme";
import {
  MicIcon, SpeakerIcon, StopIcon, ThumbDownIcon, ThumbUpIcon,
} from "./icons";

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

function greeting(name) {
  const h = new Date().getHours();
  // "-" means the user skipped the name ask; greet warmly but namelessly
  const who = name && name !== "-" ? `, ${name}` : "";
  if (h >= 5 && h < 12) {
    return { hi: `Good morning${who}`, line: "Where should today's energy go?" };
  }
  if (h < 17) {
    return { hi: `Good afternoon${who}`, line: "Time for a reset, an idea, or a plan?" };
  }
  if (h < 22) {
    return { hi: `Good evening${who}`, line: "How did today treat you?" };
  }
  return { hi: `Up late${who}?`, line: "Let's look after tomorrow-you." };
}

/* One-time, low-pressure name ask. Enter saves; "skip" never asks again. */
function NameAsk({ onSaveName }) {
  const [value, setValue] = useState("");
  return (
    <div className="name-ask rise">
      <span>What should VITAL call you?</span>
      <input value={value} maxLength={30} placeholder="First name"
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && firstNameFrom(value)) onSaveName(value);
        }} />
      <button className="name-save" disabled={!firstNameFrom(value)}
        onClick={() => onSaveName(value)}>Save</button>
      <button className="name-skip" onClick={() => onSaveName("")}>skip</button>
    </div>
  );
}

function Composer({ input, setInput, onSend, busy, hero = false }) {
  const ref = useRef(null);
  const recRef = useRef(null);
  const baseRef = useRef("");      // what was typed before the mic session
  const [micSupported, setMicSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [micNote, setMicNote] = useState(null);

  useEffect(() => { if (hero) ref.current?.focus(); }, [hero]);
  // Feature-detect after mount so SSR markup matches the first client render.
  useEffect(() => { setMicSupported(Boolean(getRecognitionCtor())); }, []);
  useEffect(() => () => recRef.current?.abort(), []);
  useEffect(() => {
    if (!micNote) return undefined;
    const t = setTimeout(() => setMicNote(null), 4000);
    return () => clearTimeout(t);
  }, [micNote]);

  function toggleMic() {
    if (listening) { recRef.current?.stop(); return; }
    const Recognition = getRecognitionCtor();
    if (!Recognition) return;
    const rec = new Recognition();
    rec.lang = navigator.language || "en-US";
    rec.continuous = true;
    rec.interimResults = true;
    baseRef.current = input;
    rec.onresult = (e) => {
      // results holds the whole session (finals + current interim); rebuild
      // the transcript each time so corrections replace, not duplicate.
      let spoken = "";
      for (let i = 0; i < e.results.length; i += 1) spoken += e.results[i][0].transcript;
      setInput(joinTranscript(baseRef.current, spoken));
    };
    rec.onerror = (e) => {
      const note = recognitionErrorText(e.error);
      if (note) setMicNote(note);
    };
    rec.onend = () => { setListening(false); recRef.current = null; };
    recRef.current = rec;
    setMicNote(null);
    try {
      rec.start();
      setListening(true);
    } catch {
      setMicNote("Voice input hit a snag. Try again.");
      recRef.current = null;
    }
  }

  function send(text) {
    recRef.current?.stop();
    onSend(text);
  }

  return (
    <>
      <div className={`composer-inner ${hero ? "hero-pill" : ""}`}>
        <textarea ref={ref} value={input} rows={1}
          placeholder={busy ? "thinking…" : listening ? "Listening…" : "Ask anything…"}
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); }
          }} />
        <button type="button" className={`mic ${listening ? "listening" : ""}`}
          aria-label={listening ? "Stop voice input" : "Start voice input"}
          aria-pressed={listening}
          title={micSupported
            ? (listening ? "Stop listening" : "Speak instead of typing")
            : "Voice input is not supported in this browser."}
          disabled={!micSupported || busy}
          onClick={toggleMic}><MicIcon /></button>
        <button className="send" aria-label="Send" disabled={busy || !input.trim()}
          onClick={() => send(input)}>↑</button>
      </div>
      {micNote && <p className="mic-note rise">{micNote}</p>}
    </>
  );
}

function Hero({ onStarter, nudge, input, setInput, onSend, busy, userName, onSaveName }) {
  const g = greeting(userName);
  return (
    <div className="hero">
      <p className="hero-hi">{g.hi}</p>
      <h2 className="hero-title">{g.line}</h2>
      <p className="hero-quote">{dailyLine()}</p>

      <Composer input={input} setInput={setInput} onSend={onSend} busy={busy} hero />

      {userName === "" && <NameAsk onSaveName={onSaveName} />}

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
      <h3>Proposed plan. Your call</h3>
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
  onSend, onDecide, onRate, nudge, autoRead = false, userName = null, onSaveName,
}) {
  const bottomRef = useRef(null);
  const [ttsSupported, setTtsSupported] = useState(false);
  const [speakingId, setSpeakingId] = useState(null);
  const autoReadRef = useRef(null);   // last reply id considered for auto-read
  const empty = messages.length === 0 && !busy;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingPlan, thinking]);

  useEffect(() => { setTtsSupported(isSynthesisSupported()); }, []);
  useEffect(() => () => { if (isSynthesisSupported()) window.speechSynthesis.cancel(); }, []);

  function speak(id, text) {
    if (!isSynthesisSupported()) return;
    window.speechSynthesis.cancel();
    const u = new window.SpeechSynthesisUtterance(stripMarkdownForSpeech(text));
    const clear = () => setSpeakingId((cur) => (cur === id ? null : cur));
    u.onend = clear;
    u.onerror = clear;
    window.speechSynthesis.speak(u);
    setSpeakingId(id);
  }

  function toggleSpeak(id, text) {
    if (speakingId === id) {
      window.speechSynthesis.cancel();
      setSpeakingId(null);
    } else {
      speak(id, text);
    }
  }

  // Auto-read ("Read replies aloud"): speak each reply once, as it completes.
  // Historical messages are marked by loadHistory and never auto-read, and a
  // reply that lands while the toggle is off stays unread when toggled on.
  useEffect(() => {
    if (busy) return;
    const last = messages[messages.length - 1];
    if (!last || last.role !== "ai" || !last.text || last.fromHistory) return;
    const id = last.id ?? messages.length - 1;
    if (autoReadRef.current === id) return;
    autoReadRef.current = id;
    if (autoRead) speak(id, last.text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, messages, autoRead]);

  return (
    <div className="chat">
      <div className="chat-scroll">
        <div className="chat-col">
          {empty && (
            <Hero onStarter={onSend} nudge={nudge}
              input={input} setInput={setInput} onSend={onSend} busy={busy}
              userName={userName} onSaveName={onSaveName} />
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
                  {ttsSupported && (
                    <button className={speakingId === (m.id ?? i) ? "chosen" : ""}
                      aria-label={speakingId === (m.id ?? i) ? "Stop reading" : "Read aloud"}
                      title={speakingId === (m.id ?? i) ? "Stop reading" : "Read aloud"}
                      onClick={() => toggleSpeak(m.id ?? i, m.text)}>
                      {speakingId === (m.id ?? i) ? <StopIcon /> : <SpeakerIcon />}
                    </button>
                  )}
                  <button className={m.rated === "up" ? "chosen" : ""}
                    aria-label="Good response"
                    onClick={() => onRate(i, "up")}><ThumbUpIcon /></button>
                  <button className={m.rated === "down" ? "chosen" : ""}
                    aria-label="Bad response"
                    onClick={() => onRate(i, "down")}><ThumbDownIcon /></button>
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
          <p className="composer-hint">VITAL can make mistakes. Plans always wait for your approval.</p>
        </div>
      )}
    </div>
  );
}
