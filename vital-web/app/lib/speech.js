/* Voice helpers — pure functions, node-testable.
 * The browser wiring (SpeechRecognition instances, speechSynthesis calls)
 * lives in Chat.jsx; everything here is deterministic string/feature logic
 * so it can run under `node --test` with no DOM. */

/* Speech-to-text constructor, if the browser has one (Chrome/Safari ship it
 * prefixed). Takes the window-like object as a param so tests can pass fakes;
 * defaults to the real window, and safely returns null during SSR. */
export function getRecognitionCtor(w = typeof window === "undefined" ? undefined : window) {
  if (!w) return null;
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function isSynthesisSupported(w = typeof window === "undefined" ? undefined : window) {
  return Boolean(w && w.speechSynthesis && typeof w.SpeechSynthesisUtterance === "function");
}

/* Splice a recognition session's transcript after whatever the user had
 * already typed, with exactly one space between. */
export function joinTranscript(base, spoken) {
  const b = (base ?? "").replace(/\s+$/, "");
  const s = (spoken ?? "").trim();
  if (!s) return b;
  return b ? `${b} ${s}` : s;
}

/* Map SpeechRecognition error codes to short human copy.
 * Returns null for codes that aren't worth surfacing (user-initiated stop). */
export function recognitionErrorText(code) {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone access was denied. Check your browser permissions.";
    case "no-speech":
      return "No speech detected. Try again.";
    case "audio-capture":
      return "No microphone found.";
    case "network":
      return "Voice input needs a network connection.";
    case "aborted":
      return null;
    default:
      return "Voice input hit a snag. Try again.";
  }
}

/* Reduce assistant markdown to something that sounds natural when read
 * aloud: drop formatting characters, keep link/emphasis text, skip code. */
export function stripMarkdownForSpeech(md) {
  if (!md) return "";
  return md
    .replace(/```[\s\S]*?```/g, " (code snippet omitted) ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")   // images → alt text
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")    // links → link text
    .replace(/^#{1,6}\s+/gm, "")                // headings
    .replace(/^\s*>\s?/gm, "")                  // blockquotes
    .replace(/^\s*([-*_]\s?){3,}$/gm, "")       // horizontal rules
    .replace(/^\s*[-*+]\s+/gm, "")              // bullet markers
    .replace(/^\s*\d+[.)]\s+/gm, "")            // ordered-list markers
    .replace(/(\*\*|__)(.*?)\1/g, "$2")         // bold
    .replace(/([*_])(.*?)\1/g, "$2")            // italics
    .replace(/~~(.*?)~~/g, "$1")                // strikethrough
    .replace(/<[^>]+>/g, " ")                   // stray html tags
    .replace(/\|/g, " ")                        // table pipes
    .replace(/[ \t]+/g, " ")
    .replace(/\s*\n\s*/g, "\n")
    .trim();
}
