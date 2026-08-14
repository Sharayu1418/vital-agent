# Security

What VITAL protects, how, and what it explicitly does not protect against.

VITAL holds sleep data, location, durable personal facts, OAuth refresh
tokens to a third party's health API, and connects strangers to each other.
Each of those is written down below with the specific mechanism, because a
control nobody can find is a control nobody can review.

---

## Reporting a vulnerability

Open a private security advisory on the repository, or email
srr10019@nyu.edu. Please don't open a public issue for anything
exploitable. This is a personal project, so expect a response in days
rather than hours.

---

## Identity — the browser never says who it is

Three caller kinds, resolved server-side in
[`security.py`](vital-app/src/vital/security.py):

| Kind | Credential | May assert a user_id? |
|---|---|---|
| `internal` | static `API_AUTH_TOKEN`, compared in constant time | Yes — this is the trusted backend caller |
| `firebase` | Firebase ID token, verified via the Admin SDK using workload identity | **No** — resolved to a stable internal id |
| `anon` | server-issued session only (HttpOnly cookie or header) | **No** |

Rules that do not soften:

- A present-but-invalid bearer token is a hard **401**. There is no
  anonymous fallback — a downgrade would silently move someone's data to a
  different identity.
- Verification failures return **one generic message**. Distinguishing
  "expired" from "wrong project" from "bad signature" hands an attacker a
  fingerprinting oracle.
- Once an anonymous session is linked to an account, the bare session stops
  resolving to it. Signing out signs you out server-side, not just in the
  browser.
- There is **no service-account JSON key** anywhere. Local development uses
  ADC; production uses the Cloud Run service identity.

---

## Actions that touch the world — security by topology

`commit_plan` is the only node that writes to your calendar. It has **no
inbound edge** except the resume from a human approval interrupt
([`graph.py`](vital-app/src/vital/graph.py),
[`planner.py`](vital-app/src/vital/planner.py)).

This matters because prompt injection is not a solved problem. A rule
saying "only commit when the user approves" is an instruction a model can
be argued out of. A missing edge is not. No sequence of tokens can route to
a node the graph does not connect.

The same principle covers the buddy board: identity is passed in
explicitly by the route layer and a request body can never name whose post
is created, updated, or decided.

---

## Model-written code

When someone asks a statistical question about their sleep, a model writes
pandas and it runs. Two independent layers
([`sandbox.py`](vital-app/src/vital/sandbox.py)):

1. **An AST-based static gate, before execution.** Prompt injection can
   make a model write hostile code; it cannot make a Python parser approve
   it. Imports, attribute access and URL literals are checked here.
2. **An E2B Firecracker microVM.** No secrets, no access to our filesystem
   or infrastructure, torn down per invocation.

**Honest limit:** E2B sandboxes may have outbound internet on the free
tier. Exfiltration control is therefore the static gate's URL ban plus
E2B's network configuration — not the VM boundary. This is documented in
the module rather than assumed away.

---

## Secrets

- Every secret lives in **Google Secret Manager**, mounted at deploy time.
  Nothing sensitive is in the repository, in Vercel, or in any
  `NEXT_PUBLIC_*` variable — those ship inside the client bundle, and
  marking them "sensitive" in a dashboard changes nothing.
- **OAuth refresh tokens are encrypted at the application layer** with
  Fernet, keyed from Secret Manager
  ([`secrets.py`](vital-app/src/vital/secrets.py)). Cloud SQL is already
  encrypted at rest; that protects the disk, not a leaked backup, an
  over-broad grant, or an injection. A refresh token is a live bearer
  credential to months of somebody's health history.
- Encryption **refuses rather than degrading**. If the key is missing, the
  connect route returns 503 instead of storing credentials in plaintext —
  a silent downgrade would look identical to working.
- No secret has ever been committed. `.env`, `*.pem` and credential JSON
  are gitignored, and the history is clean.

---

## Linking a wearable

The OAuth `state` parameter is signed, session-bound and expiring
([`oauth_state.py`](vital-app/src/vital/oauth_state.py)).

Without it, an attacker completes consent with their own Fitbit account,
delivers the resulting code to a victim's browser, and the victim's VITAL
account is linked to the **attacker's** health data — silently driving
their forecast and every plan built on it. That is login CSRF, and the
state parameter is the standard defence.

The callback derives identity from the session that started the flow, never
from anything in the URL. Signature verification happens before any field
inside the payload is read, and comparisons use `compare_digest`.

Disconnecting revokes upstream at the provider, deletes the token, and
erases every night that provider wrote — scoped by source, so manual logs
and the user's own uploads survive.

---

## Health data and privacy

- Users can see everything VITAL remembers about them and delete any of it.
- Uploads stream to a temporary file with a size cap; the whole file is
  never held in memory.
- Log lines carry **hashed** user identifiers. Anonymous session ids are
  identity too, and are hashed on the same path.
- Coordinates are rounded to 2dp (~1.1km) **server-side** before storage.
  Coarse enough that they cannot identify a home, precise enough to rank
  venues across a city. Rounding client-side only would be a request, not a
  guarantee.
- The buddy meeting-point document contains distances, never positions, and
  both people receive the identical file — so everything in it is something
  each has agreed the other may see.

---

## Abuse and safety between users

- **Blocking** works in both directions and is enforced before any request
  row is written. A blocked owner's post reads as a plain 404, so blocking
  is never disclosed.
- **Reports** deduplicate per reporter, auto-hide a post at three distinct
  reporters, and raise an alert through the tool-health metric. Three, not
  one — a single complaint must never remove somebody, or the safety
  feature becomes a weapon.
- A hidden post reads as **not found**, not "under review". Telling a
  reported user what happened tells them roughly who did it.
- Free-text fields are scrubbed of emails and phone numbers before storage.
  Location granularity is city and area; there is nowhere to put an
  address.
- **Crisis language** is screened on every message by a cascade — a broad
  regex net, then a classifier with a 4-second ceiling, then a
  deterministic keyword floor if the model is slow or down. Keywords alone
  caught 13.3% of a labelled set; the cascade catches 100%. The floor
  exists so a distressed person is never left on a spinner.

---

## Availability and cost

- **Per-user daily token budget**, billed from real provider usage rather
  than a character estimate. The old estimate undercounted by 150×, so the
  effective cap was far looser than the configured number implied.
- **Per-identity request limits**
  ([`ratelimit.py`](vital-app/src/vital/ratelimit.py)), keyed on the
  resolved identity after authentication — never on anything the client
  sends, which could otherwise be changed to reset the counter.
- **The scheduled job endpoint** takes a shared secret compared in constant
  time and **fails closed** when unconfigured. An empty configured token
  must not mean "let everyone in".

**Honest limit:** rate limiting is in-process. Across several Cloud Run
instances the effective ceiling is roughly the configured one times the
instance count. It stops a single client in a loop, which is what actually
happens. A distributed flood needs Cloud Armor or a shared counter, and
neither is proportionate to this application's scale.

---

## What VITAL does not defend against

Stated plainly, because a threat model that only lists wins is marketing.

- **A compromised Google account.** Firebase sign-in means Google is the
  root of trust.
- **A distributed denial of service.** See above.
- **A malicious operator.** Anyone with production access can read the
  database. There is no field-level encryption of health data beyond the
  OAuth tokens, and no separation of duties — it is a single-maintainer
  project.
- **Model output being wrong.** Grounding checks verify that named venues
  came from tool results, and the crisis cascade fails safe, but VITAL can
  still give unhelpful advice. It is explicitly not a medical device.
- **Traffic analysis.** Everything is TLS, but request timing and size are
  not padded.
- **Supply chain.** Dependencies are pinned by lockfile and not currently
  scanned automatically. That is the most obvious next addition.
