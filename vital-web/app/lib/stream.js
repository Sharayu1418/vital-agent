/* Pure SSE parsing + stream-state reduction, extracted from page.jsx so the
 * two bugs it had are pinned by node --test regression tests:
 *
 * 1. trimStart() on data lines violated the SSE spec (strip exactly ONE
 *    leading space) — token chunks starting with a space rendered as
 *    "Iunderstand" instead of "I understand".
 * 2. Late empty `message`/terminal events clobbered streamed text, then the
 *    cleanup deleted the "empty" assistant bubble.
 */

export function initialStream() {
  return { text: "", status: null, plan: null, approval: false };
}

export function applyEvent(st, { event, data }) {
  switch (event) {
    case "token":
      return { ...st, text: st.text + data, status: null };
    case "message":
      // empty message events (terminal/heartbeat edge cases) must NEVER
      // overwrite text that already streamed
      return data ? { ...st, text: data, status: null } : st;
    case "status":
      return { ...st, status: data };
    case "approval_required":
      return { ...st, approval: true, plan: JSON.parse(data).plan };
    default:
      return st; // done, unknown events: no state change
  }
}

export function shouldKeepBubble(st) {
  return st.text.length > 0;
}

// SSE wire format: "data: payload" — the spec strips exactly ONE leading
// space after the colon. Payloads may legitimately begin with whitespace.
function stripFieldSpace(v) {
  return v.startsWith(" ") ? v.slice(1) : v;
}

export function parseFrame(frame) {
  let event = "message";
  const data = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = stripFieldSpace(line.slice(6)).trim();
    else if (line.startsWith("data:")) data.push(stripFieldSpace(line.slice(5)));
  }
  return { event, data: data.join("\n") };
}

export async function* sseEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split(/\r?\n\r?\n/);
    buf = frames.pop() ?? "";
    for (const frame of frames) yield parseFrame(frame);
  }
}
