# What you need to do manually

Everything I could do is committed on `fix/week1-stop-the-bleeding` (7 commits, 262 backend / 104 frontend tests passing). This is the list of things that need your credentials, your cloud console, or your judgement.

Ordered. Step 1 blocks the deploy.

---

## 1. Set `NEXT_PUBLIC_API_BASE` in Vercel — do this BEFORE pushing

**Vercel → Project → Settings → Environment Variables**, set it for **both Production and Preview**, pointing at your Cloud Run URL.

This is now load-bearing: the build **fails** if it's missing. That's deliberate (P1-11) — previously a missing variable built fine and shipped a bundle pointing at `localhost:8000`, which died at runtime with a generic "Can't reach the backend" and no signal to you. But it does mean an unset variable now blocks the deploy instead of silently breaking it.

## 2. Push

```bash
cd ~/Downloads/VITAL
git push -u origin fix/week1-stop-the-bleeding
```

## 3. Set two backend env vars on Cloud Run

**`PREVIEW_ORIGIN_REGEX`** — without it, Vercel preview deployments stay CORS-blocked and look completely dead. Open any real preview URL and copy its shape:

```
PREVIEW_ORIGIN_REGEX=^https://vital-agent-git-[a-z0-9-]+-yourteam\.vercel\.app$
```

Keep it tight, and note the service refuses to boot if the pattern is unanchored or contains `.*`. That's not pedantry: these are credentialed requests, `*.vercel.app` is a shared namespace, and a loose pattern would let someone else's project make authenticated requests as your users. That's also why I didn't auto-derive it for you.

**`CRISIS_TIMEOUT_SECONDS`** — optional, defaults to 4.0. Only change it if you see crisis screens timing out in the logs.

## 4. ~~Enable HTTP/2 on the Cloud Run service~~ — DO NOT DO THIS

**This advice was wrong and caused a production outage on 4 Aug. Superseded.**

Cloud Run caps HTTP/1.1 request bodies at 32MB, and I recommended `--use-http2` to lift it. But enabling HTTP/2 makes Cloud Run forward requests to the container as **h2c (HTTP/2 cleartext)**, and **uvicorn does not speak h2c** — only HTTP/1.1. Every request then dies at the proxy:

```
upstream connect error or disconnect/reset before headers. reset reason: protocol error
```

I checked the Cloud Run limit and never checked whether the container could accept the protocol it implies. If HTTP/2 is currently on, turn it off:

```bash
gcloud run services update vital-api --region us-central1 --no-use-http2
```

**What this means for large uploads.** The 32MB ceiling stands. The streaming/OOM work still matters and still holds — an oversized upload now returns a clean 413 instead of killing the container — but Apple Health exports above 32MB are not supported until one of:

- **Signed-URL upload straight to GCS**, with the backend reading the object afterwards. Correct architecture, removes the request-size limit entirely, and is the fix I'd recommend.
- **Swap uvicorn for hypercorn**, which does support h2c. Changes your serving stack to work around a limit the option above removes properly.

Neither is required for anything else in this batch.

## 5. Run the live crisis eval — this is the number that matters

```bash
cd vital-app
CRISIS_LIVE_EVAL=1 uv run pytest tests/test_crisis_live.py -s
```

Needs Vertex credentials, which is why I can't run it. It prints recall, precision, and **every individual miss and false fire** against 45 distressed phrasings and 31 hard negatives.

The offline number I reported (88.9%) is only the *outage floor* — what runs when the model is unreachable — and it's partly self-graded, since I wrote both the patterns and the test cases. This command produces the real one.

If it fails: read the misses. Each is a genuine gap, and the fix is usually a line in `CLASSIFIER_PROMPT` in `guardrails.py`. Re-run until recall is where you want it. **Do not lower the threshold to make it pass.**

## 6. Deploy, then smoke test

Full checklist in `WEEK1-HANDOFF.md`. The four that matter most, because no test can prove them:

- **Upload a real Apple Health `export.zip`** and watch container memory. It should stay flat. This is the OOM fix.
- **Two accounts at once**: long chat in one, load the panel in the other. Neither should stall.
- **Send a message, immediately click another chat.** The answer must not follow you.
- **Send a message and hit stop.** Partial text stays, composer re-enables.

## 7. Read `undercount_ratio`, then decide the budget

After a few real turns, in Cloud Logging:

```
jsonPayload.metric="chat_turn"
```

Each entry carries `est_tokens` (real, billed), `heuristic_tokens` (the old estimate), and `undercount_ratio` between them. I left `DAILY_TOKEN_BUDGET` at 50,000 deliberately rather than guess a new number — this ratio tells you what the cap has actually been allowing. My estimate was 10–30x; the logs will say.

---

## Still open in the audit (not started)

- **P1-6** — conversation history grows unbounded; long threads will eventually fail mid-conversation.
- **P1-8** — two agents in one turn concatenate into a single bubble with no separator.
- **P1-13** — long analysis turns stream nothing for 30–60s.
- **P1-14** — cold-start schema setup on every scale-from-zero.
- **P2-15 → P2-19** — smaller items.
- **A-1 through A-5** — the five product recommendations. A-1 (energy forecast) is the one I'd build next.
