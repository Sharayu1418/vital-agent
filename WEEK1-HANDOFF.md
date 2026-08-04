# Week 1 — Stop the Bleeding: handoff

Branch: `fix/week1-stop-the-bleeding` (off `main`)

**Test results**

| Suite | Before | After |
| --- | --- | --- |
| Backend (`pytest`, excl. 2 env-broken tool files) | 220 passed | **241 passed** |
| Frontend (`node --test`) | 93 passed | **97 passed** |

Every new test was mutation-checked: I broke the code it guards and confirmed the test fails, then restored. A test that passes either way is worse than no test.

---

## 1. Commits — done

All four are on the branch, working tree clean:

```
afa6faa  Keep a streaming answer in the thread it was sent from
c46491a  Stream health uploads and accept Apple's export.zip
4fcf4f4  Bill real provider token counts instead of a chars/4 guess
c5092aa  Stop blocking the event loop on synchronous storage calls
```

**Nothing is pushed.** The sandbox has no network route to GitHub and no access to your SSH key, so the push is yours:

```bash
cd ~/Downloads/VITAL
git push -u origin fix/week1-stop-the-bleeding
```

Do step 2 first — `pnpm build` especially.

---

## 2. Run the suites yourself

My sandbox only has Python 3.10 (the project wants 3.11+) and the pinned `swc` binary is macOS-only, so I could not run `pnpm build`. Please confirm on your machine:

```bash
cd vital-app && uv run pytest && uv run ruff check .
cd ../vital-web && pnpm test && pnpm build
```

Two things I could not verify and you should watch for:

- **`pnpm build`** — I syntax-checked all four changed JS/JSX files with esbuild and they parse, but a real Next build never ran.
- **`ruff`** — my ruff (0.16) reports 25 pre-existing issues in `api.py` on both the old and new code, because its default ruleset is broader than your pinned `ruff>=0.8`. Your pinned version should be clean, but confirm the count didn't change.

---

## 3. Manual smoke checklist

No GCP or Firebase credentials here, so none of this was exercised against a live stack.

**P0-1 — event loop**

- [ ] Open two browsers signed in as different accounts. Start a long chat in A; while it streams, load the side panel in B. B should respond immediately, and A's stream should not stutter.
- [ ] Confirm `/sleep/recent`, `/calendar`, `/memories` still return the right user's data.

**P0-2 / P0-3 — upload**

- [ ] Upload a real `export.zip` from Apple Health (Health app → profile → Export All Health Data). Should import without unzipping.
- [ ] Upload a bare `export.xml`. Same result.
- [ ] Upload a normal sleep CSV — unchanged behaviour.
- [ ] Upload something over 500MB → clean `413`, and **watch container memory**: it should stay flat instead of spiking. This is the OOM fix.
- [ ] Upload a corrupt zip and a truncated XML → `422` with a readable message, not `500`.
- [ ] **If files over 32MB fail before reaching the app**, that's the Cloud Run HTTP/1.1 limit, not this code. Redeploy with HTTP/2 enabled.

**P0-5 — token accounting**

- [ ] Send a few messages, then check Cloud Logging for `chat_turn` entries. Compare `est_tokens` (real) against `heuristic_tokens` and read `undercount_ratio`. **This is the number to bring back before changing the cap.** My estimate was 10–30x; the logs will tell you the truth.
- [ ] Temporarily set `DAILY_TOKEN_BUDGET` low, send a message that triggers a multi-agent turn, and confirm the answer stops partway with the limit notice appended rather than replacing what already streamed.

**P1-7 — thread guard + stop**

- [ ] Send a message, then immediately click another chat. The answer must **not** appear in the new chat. Go back — it should be in the original thread.
- [ ] Send a message and hit stop. Partial text stays, composer re-enables immediately.
- [ ] Sign out mid-stream. No stray bubbles, no console errors.
- [ ] Approve a plan, then switch threads while it commits. The plan card must not appear in the other thread.

---

## 4. Follow-ups this opened

- **`DAILY_TOKEN_BUDGET` is now measurable but unchanged at 50,000.** Left deliberately — see the ratio in the logs first, then set it on purpose.
- **Cloud Run HTTP/2** is a required deploy change for the upload work to fully land.
- **P1-9 (parallel session race)** is adjacent to code I touched but out of scope; `refreshPanel`'s `Promise.all` can still mint three anonymous sessions on first load.
- I did **not** touch `vital-mobile/`. It calls `/upload/health` and `/chat`; both contracts are backwards-compatible (the upload response shape is unchanged, and `signal` is an optional argument), so nothing should break — but it's untested.
