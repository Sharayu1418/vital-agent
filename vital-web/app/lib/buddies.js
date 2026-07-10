/* Activity Buddy Board — pure helpers, node-testable.
 * Payload shaping and safe-field filtering live here so tests can pin the
 * exact wire format and what a match card is allowed to render. */

export const REQUIRED_FIELDS = ["display_name", "activity", "city"];

export const TEXT_FIELDS = [
  "display_name", "activity", "city", "area", "time_window",
  "vibe", "skill_level", "budget", "group_size", "notes",
];

/* Option lists for the form selects — free-text is allowed server-side,
 * these just keep the UI low-friction and consistent. */
export const TIME_WINDOWS = ["weekend", "weekday evening", "weekday daytime", "flexible"];
export const VIBES = ["casual", "beginner-friendly", "accountability", "social"];
export const SKILLS = ["beginner", "intermediate", "advanced"];
export const BUDGETS = ["free", "low", "moderate"];
export const GROUP_SIZES = ["2", "2-4", "4-8", "any"];

/* Shape a create/update payload: trim everything, drop empty optionals,
 * report missing required fields instead of sending a doomed request. */
export function buildPostPayload(form) {
  const payload = {};
  for (const k of TEXT_FIELDS) {
    const v = (form?.[k] ?? "").toString().trim();
    if (v) payload[k] = v;
  }
  payload.active = form?.active !== false;
  const missing = REQUIRED_FIELDS.filter((k) => !payload[k]);
  return { payload, missing, ok: missing.length === 0 };
}

/* Whitelist of what a buddy card may render — defense in depth: even if
 * the API ever over-shares, nothing outside this list reaches the DOM. */
export const SAFE_CARD_FIELDS = [
  "id", "display_name", "activity", "city", "area", "time_window", "vibe",
  "skill_level", "budget", "group_size", "notes", "created_at", "owner_key",
  "mine", "active", "pending_requests", "match_score", "match_reasons",
];

export function safeCard(post) {
  const out = {};
  for (const k of SAFE_CARD_FIELDS) {
    if (post?.[k] !== undefined) out[k] = post[k];
  }
  return out;
}

/* Build ?activity=…&city=… from non-empty filters only. */
export function searchQuery(filters) {
  const params = new URLSearchParams();
  for (const k of ["activity", "city", "time_window", "skill_level", "budget", "vibe"]) {
    const v = (filters?.[k] ?? "").toString().trim();
    if (v) params.set(k, v);
  }
  return params.toString();
}

/* Honest about what's visible: area/notes ARE public — that's the point of
 * the board. Don't promise location privacy; tell people what not to post.
 * Keep in sync with SAFETY_NOTE in vital-app/src/vital/buddies.py. */
export const SAFETY_NOTE =
  "Meet in public places and tell someone where you're going. Only the city, " +
  "area, and notes you choose to post are visible — don't include exact " +
  "addresses or contact details (VITAL removes any it spots).";
