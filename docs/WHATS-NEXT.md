# What's next for VITAL, and why

A working plan. Each item says what it is, why it's worth doing, what you'd
say if someone asked you about it in an interview, and which tools it uses.

Read it end to end once. Then we pick one and build it.

---

## Where things actually stand

Done and working: four agents, semantic memory, crisis detection, the
approval gate, the energy forecast, Fitbit sync (built, not connected),
buddy matching, moderation, rate limiting, CI, and five eval suites.

The big shift that just happened: venue recommendations used to be "Google
returns 5 places, the model picks 3". Now it's "Google returns 40, a
scoring function ranks them, the model explains the top 3". The model
stopped making the decision. That's the change that matters most for how
this project reads.

What's still missing is mostly measurement and one or two loops that would
make the app improve as you use it.

---

## 1. Measure how good retrieval actually is

**What it is**

Your memory system does semantic search. Ask about ceramics, it should find
the pottery fact. Right now you have tests proving that deduplication works
and that the threshold is calibrated. You have nothing proving that recall
is good.

Same for venue search. You fetch 40 candidates and rank them, but nobody has
checked whether the right venue was in those 40 to begin with. If retrieval
misses it, no amount of clever ranking helps.

So: build a labelled set. Maybe 30 queries with the answers you'd expect,
and measure two numbers. Recall@k is "was the right thing in the top k".
MRR is "how high up was it". Run it like the other evals, on demand.

**Why it helps**

You can't improve what you don't measure, and retrieval is the part of a RAG
system most likely to be quietly bad. Everyone tests that their vector store
returns *something*. Almost nobody tests that it returns the *right* thing.

It also protects you. If you change the embedding model, or the threshold,
or how facts are phrased, this tells you immediately whether it got worse.

**What you'd say in an interview**

"The thing I see people skip in RAG projects is retrieval evaluation. They
test that the pipeline runs, not that it retrieves well. So I built a small
labelled set and measure recall@k and MRR. It caught that my memory facts
all started with 'User', which was hurting similarity — every fact looked
alike to the embedder."

That last part may or may not turn out to be true, but it's the kind of
thing this eval finds, and finding something is the point.

**What we'd use**

- Existing pgvector setup, no new infrastructure
- A `retrieval_cases.py` file, same shape as the crisis and answer case sets
- Plain Python for the metrics, about 40 lines
- Gated behind an env var like the other live evals, since it needs real
  embeddings

**Effort:** half a day.

---

## 2. Pull real attributes out of reviews

**What it is**

Right now the ranker matches your preferences against a venue's name and
Google's type labels. So "climbing" matches a place called "Albany Boulder
Co" or typed `rock_climbing`. That's thin.

Reviews contain the actual useful information. "Good for beginners." "Gets
loud after 8pm." "The instructor is patient." "Parking is a nightmare."
None of that is in the structured fields.

So: fetch the top reviews for a venue, run them through a model with a
strict schema, and get back a list of attributes. Cache the result, because
reviews don't change hourly and you don't want to pay for this on every
search.

**Why it helps**

Two reasons. Ranking gets better, because now "quiet" or "beginner friendly"
can actually match something. And explanations get specific — "several
reviews mention it's good for beginners" beats "this is a great gym".

It's also the difference between using an API and building something on top
of one.

**What you'd say in an interview**

"I had a structured data problem. Google gives you ratings and categories,
which isn't enough to say why a place suits someone. So I built an
extraction pipeline: pull reviews, run them through a model with a Pydantic
schema so the output is validated, cache by place ID with a TTL. The caching
matters — without it I'd be making a model call per venue per search, which
is forty calls for one question."

**What we'd use**

- Google Places reviews (already have the API key, just a field mask change)
- Gemini Flash for extraction, same model already in use
- Pydantic for the output schema, so malformed output fails loudly
- A `venue_attributes` table for the cache, keyed on place ID
- Existing `metrics.log_tool` so extraction failures show up in alerting

**Effort:** a day.

---

## 3. Let the recommender explore

**What it is**

Your ranker always shows the top 3. That sounds right and it's a trap.

If you only ever see rank 1, 2 and 3, you never find out whether you'd have
liked rank 7. And the system never learns anything it didn't already assume.
This is the classic explore/exploit problem in recommendation systems.

The fix is small. Most of the time, show the top 3. Sometimes — say one in
five — swap the third slot for something further down the list. Then when
feedback comes in, it has variety in it.

**Why it helps**

Without exploration, your preference model just confirms itself forever. You
like climbing, so it shows climbing, so you engage with climbing, so it's
more confident you like climbing. You never discover the pottery studio.

Practically it's also just better product. Three predictable results get
boring.

**What you'd say in an interview**

"A recommender that always shows its top result generates no information.
I added epsilon-greedy exploration on the third slot — 20% of the time it's
a deliberate gamble from further down the ranking. It's about thirty lines
of code, but it's the difference between a system that can learn and one
that can only confirm what it already thinks."

If they push: Thompson sampling would be the more sophisticated answer, but
it needs enough data to estimate distributions, and with one user
epsilon-greedy is honest.

**What we'd use**

- Pure Python, no dependencies
- The existing `suggestion_log` table to record what was shown and whether
  it was an exploration pick
- Tests asserting the exploration rate is roughly right over many runs

**Effort:** two hours.

---

## 4. Make feedback actually change something

**What it is**

You have thumbs up and down. They write to a table. Nothing reads it, except
the digest script I wrote, which a human has to run.

Close the loop. When someone thumbs up a suggestion, or says "I went", nudge
the ranking weights in that direction. Thumbs down, nudge away.

Start simple. Don't build a neural network. Adjust the preference terms and
maybe the signal weights, per user, stored in a table.

**Why it helps**

This is the thing that makes the app feel like it knows you. It's also the
only way any of the ranking work compounds — right now the weights I chose
are just my opinion, frozen.

**What you'd say in an interview**

"I had a feedback signal being collected and thrown away, which I think is
the most common waste in these apps. So I wired thumbs up/down back into the
ranking weights. Deliberately simple — a per-user adjustment, not a learned
model — because with one user there isn't enough data to fit anything, and a
complicated model I can't debug is worse than a simple rule I can."

**What we'd use**

- A `user_ranking_weights` table
- The existing feedback endpoint, extended to say which venue it was about
- Bounded adjustments so one angry click doesn't wreck the ranking
- Tests that weights stay in range and that a single vote can't dominate

**Effort:** half a day.

---

## 5. Make venues actually visible

**What it is**

You said this and you're right. Venues are currently markdown links inside a
chat bubble. For a product about finding places, that's weak.

Cards. Photo, name, distance, why it was ranked, a rating, a link to maps.
Maybe a small map. The ranking signals could show as a little bar so you can
see *why* it's first.

**Why it helps**

It's the most visible change on this list. Someone looking at the app for
thirty seconds sees this, not your scoring function.

It also makes the ranking legible. If you can see "energy fit 0.94, 1.2km,
matches climbing", the recommendation stops being a black box.

**What you'd say in an interview**

"I made the ranking visible in the UI rather than hiding it. Each suggestion
shows the signals behind it. Partly that's honesty — the user can see why —
and partly it's that I found bugs in the ranker faster once I could see the
numbers next to the results."

**What we'd use**

- Google Places photos (field mask change, photo reference then a fetch)
- React components, same patterns as the existing cards
- Possibly a static map image rather than an interactive map, since an
  interactive one is a whole dependency for something you'd glance at

**Effort:** a day, mostly frontend.

---

## 6. Use the coordinates we already have

**What it is**

A small accuracy fix that's been sitting there. The browser sends exact
coordinates on every request. But `get_weather` takes a city *name*, and so
does the old `search_places`. So the model guesses "Albany" from
conversation, and we geocode that word, when we had the actual position all
along.

**Why it helps**

Weather for where you are, not where the model thinks you are. It's a small
thing that removes a whole class of wrong answers.

**What you'd say in an interview**

"I had a bug where the side panel showed one city and the chat used another.
Turned out location lived in two places — browser storage for the UI, and
the model's memory for the tools — and they never talked. I made location
travel with the request and told the agent it overrides stored memory,
because where you are today is a fact about today, not a stable trait."

That's a good story. It's already half-done, so finish it.

**What we'd use**

- OpenWeather accepts `lat`/`lon` directly
- Places already has `search_near` with a location bias, written for the
  meetup PDF
- The `storage.current_location` contextvar that already exists

**Effort:** half a day.

---

## 7. Write plans to a real calendar

**What it is**

Approved plans currently go into VITAL's own table. Nobody looks at that.
So a plan you approved dies there and you re-enter it manually, which means
most plans just die.

Google Calendar write access, using incremental OAuth on the sign-in you
already have.

**Why it helps**

It's the difference between a planner and a plan-shaped conversation.

The safety story is already handled, which is the interesting part. The node
that writes to a calendar has no path to it except your approval click. So
adding a real calendar doesn't add risk, it swaps the implementation behind
an interface that already exists.

**What you'd say in an interview**

"Write actions are where these systems get dangerous, so I made it
structural rather than a rule. The commit node has no inbound edge except
the human approval resume — it's not that the model is told not to write, it
literally can't reach the code path. That meant adding real calendar access
was swapping an implementation, not redesigning the safety model."

**What we'd use**

- Google Calendar API, incremental auth on the existing Firebase Google
  sign-in
- The `make_commit_plan(calendar)` injection point that's already there
- The same encrypted token storage built for Fitbit
- Keep the `plan_hash` idempotency, so a double-click doesn't double-book

**Effort:** a day and a half, plus OAuth scope setup.

---

## 8. Scan dependencies

**What it is**

`SECURITY.md` currently admits dependencies aren't scanned. Fix that.
Dependabot on the repo, `pip-audit` in CI.

**Why it helps**

It's the obvious next question after reading that file, and it's twenty
minutes.

**What you'd say in an interview**

Not much. It's table stakes. But its absence is noticeable, and having
written down that you knew it was missing is better than not noticing.

**What we'd use**

- GitHub Dependabot, config file only
- `pip-audit` as a CI step
- `pnpm audit` for the frontend

**Effort:** half an hour.

---

## Parked until you have a wearable

**HRV and resting heart rate.** The Google Health API exposes both, and
they're the strongest readiness signals in consumer wearables — it's what
Oura and Whoop build their scores from. Adding them would make the forecast
genuinely competitive rather than a sleep-duration model.

Needs a device and a re-consent for extra scopes. Worth doing the day you
have one, not before.

**An iOS app.** The only way Apple Watch data reaches a server. Apple runs
no cloud API for HealthKit, so it needs a native app reading on-device and
uploading. That's a separate project, not a feature.

---

## What I'd do in what order

If you want the project to *read* better: 5, then 2, then 1.

If you want it to *be* better: 1, then 2, then 4, then 3.

If you want the strongest interview material for the least work: 1 and 3.
Retrieval evaluation and exploration are both small, both specific, and both
things most candidates haven't done.

My actual recommendation is 1 first. Not because it's exciting, but because
everything else on this list changes retrieval or ranking, and right now you
have no way to tell whether a change made things better or worse. Build the
measuring stick before you start cutting.

---

## One thing worth remembering

The pattern that caused nearly every bug in this project: **something built
to check the system was doing something different from the system.**

The memory threshold was calibrated on one scale and enforced on another.
The CORS test passed while the browser failed. The answer eval graded a
model with no tools. Five separate incidents, same shape.

So whatever you build next, ask one question: does this measure the thing
production actually does, or does it measure my idea of it? That question
would have saved most of a day.
