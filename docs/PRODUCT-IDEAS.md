# Making VITAL something people actually want to use

Ideas ranked by (value to a user) ÷ (effort), with the reasoning shown so
you can disagree with the ranking rather than just the conclusion.

The honest starting position: VITAL is currently a very good tool that you
have to remember to open. Almost everything below is about closing that gap.

---

## The one that matters most: VITAL only exists when the tab is open

Every genuinely useful life tool initiates. Yours cannot. You could have the
best energy forecast in the world and still lose to a habit nobody formed.

**A morning brief.** One notification: last night's sleep, today's predicted
curve, what's on your committed plan, and *one* concrete adjustment. Not a
dashboard — a sentence you can act on.

> "6h10 last night, third short one in a row. Your peak's around 10:40 —
> put the hard thing there. I'd move the 6pm run to tomorrow."

The infrastructure exists: Cloud Scheduler → a Cloud Run job → the existing
graph. The hard part isn't technical, it's restraint. **One per day, opt-in,
always dismissible.** The moment it becomes two, people turn it off and never
come back.

**Why this beats every other feature on this list:** it converts a
session-based tool into a habit, and it's what makes the memory and forecast
investments pay off. Both are currently only visible to someone who
remembers to come looking.

---

## The cheapest big win: close the loop on the forecast

Right now the forecast makes a claim and nothing ever checks it. That's a
missed opportunity twice over — the user never learns whether to trust it,
and you never get the data to make it better.

**Harvest feedback from what people already say.** You said no manual entry,
and you're right. But "I'm wrecked today" in chat *is* a label. The
message-screening path already exists for crisis detection; the same shape
can spot energy mentions and pair them with what was predicted for that
hour.

Zero friction, no forms, and after a few weeks you can say:

> "I've been predicting your afternoons about right, but I've been
> underestimating your evenings — you're sharper after 8pm than the model
> thought. I've adjusted."

**That sentence is the product.** It's the moment it stops being a chatbot
with a chart and becomes something that knows you. Nothing else on this list
produces that feeling as cheaply.

---

## Things that make it feel human rather than clever

**Let it say "don't".** The most trustworthy thing a wellness app can do is
tell you to do less. Three short nights in a row should produce "today is a
recovery day, I'm not going to suggest anything ambitious" — and then
actually not suggest anything ambitious. Everything currently pushes toward
activity, which is what makes these apps exhausting.

**Remember the outcome, not just the plan.** You commit a plan; nobody ever
asks how it went. One lightweight follow-up — "did the climbing happen?" —
turns memory from a list of facts into a record of what actually works for
you. It also generates exactly the signal that makes suggestions better.

**Name the tradeoff out loud.** The planner already has a `tradeoffs` field
and the meeting-point PDF states travel costs plainly. Extend that
everywhere. "I put this here because you're sharpest then, which means
dinner moves later" reads as a colleague. Silent optimisation reads as a
machine.

**Let people correct it.** The memory panel has a delete button but no edit.
"That's wrong, I moved" should be a one-tap fix, not a delete-and-hope. Being
able to correct a system is what makes people trust it with more.

---

## Navigation and design

The current layout is three columns: threads and memory left, chat centre,
panel right. It works on a laptop and it's crowded on anything smaller.

**The strongest single change is a hierarchy of attention.** Right now the
sidebar gives the energy curve, devices, memory and threads roughly equal
weight. They aren't equal. Suggested order:

1. **Today** — the forecast curve, and one line of what it means
2. **Your plan** — what you committed to
3. Everything else, collapsed by default

**Make the forecast the first thing, not a sidebar widget.** It's the
differentiator and it's currently smaller than the chat list. On open, the
first thing you see should be your day.

**One thing to stop doing:** the empty states currently say "Nothing saved
yet" and "No sleep data yet." Four of those stacked reads as a broken app. An
empty state should say what to do next, not report absence.

---

## Latency: what's actually left

Most of the wins are already taken — routing (5×), the crisis screen
overlapped, history trimmed, and the wearable sync just moved off the
response path. What remains:

- **Perceived, not actual.** The status vocabulary now fills the dead air
  before the first token. That's worth more than shaving 200ms.
- **The panel makes five parallel calls on load.** They're already
  `allSettled`, but the forecast could be served from the last known value
  instantly and refreshed underneath.
- **Memory recall costs an embedding call before every agent turn.** Caching
  it per turn is a small, safe win.
- **Measure before optimising further.** `duration_ms` is already in the
  `chat_turn` logs with a p95 target of 2500ms. Read it before guessing.

---

## Things I'd deliberately not build

**A social feed.** The buddy board works because it's request-to-join with
no DMs and approximate areas. A feed would need moderation you don't have.

**Streaks or gamification.** VITAL's whole tone is "you don't have to earn
your evening." A streak counter contradicts that on every screen, and it's
the fastest way to make a wellness app feel like a boss.

**More agents.** Four is already enough that routing needs a hop guard. A
fifth adds a routing failure mode before it adds value.

**Another wearable provider before someone owns the device.** The seam
exists so Oura is one file, but an integration nobody can test end to end is
the Reddit failure recreated on purpose.

---

## If you only do three things

1. **The morning brief.** It's the difference between a tool and a habit.
2. **Harvest energy labels from chat.** It's what makes v2 of the forecast
   possible, and "I've been underestimating your evenings" is the single
   most compelling sentence this app could say.
3. **Put the forecast first in the layout.** The differentiator shouldn't be
   the fourth thing on the page.
