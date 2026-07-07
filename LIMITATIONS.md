# Known limitations (honest list — this builds more trust than hiding them)

**Identity.** Anonymous sessions are cookie-bound: clear cookies, lose your
data. Trusted callers share one bearer token (app-level, not per-user).
Real per-user auth (Clerk/Firebase) is the next milestone.

**Memory.** Keyword-overlap retrieval, not semantic — "ceramics" won't match
a stored "pottery" fact until pgvector embeddings land. Extraction quality
depends on Flash; junk facts are possible (the memory drawer's delete button
is the mitigation).

**Sandbox.** The AST gate is defense-in-depth, not a guarantee; E2B free-tier
VMs may have outbound internet, so exfiltration control is the URL ban +
(in prod) E2B network restrictions. Apple Health parsing loads the whole XML
into memory — large multi-year exports need the streaming parser.

**Calendar.** Local table only; nothing writes to Google Calendar yet (OAuth
deliberately deferred).

**Events/communities.** Ticketmaster skews to ticketed events (concerts,
sports) — hobby meetups are underrepresented. Reddit search is unauthenticated
and rate-limited; it will intermittently degrade.

**Costs/limits.** Token accounting is a chars/4 estimate, not billed usage.
Budget resets at midnight UTC, not user-local midnight.

**Ops.** Default local run uses MemorySaver — approval pauses don't survive a
restart without DATABASE_URL. Single region. Evals cover routing well, tool
correctness partially, plan quality not yet (LLM-as-judge is scaffolded, not
wired).

**Wellness scope.** VITAL is not a medical or mental-health tool. The crisis
path detects a curated phrase list only; messages outside it won't trigger
resources.
