/* Generation guard for identity-scoped async work — pure, node-testable.
 *
 * Problem it solves: a fetch started while account A was signed in must
 * not write into React state after sign-out or a switch to account B.
 * Every load captures a liveness check; every identity change bumps the
 * generation, which flips all previously captured checks to false.
 *
 *   const guard = createGenerationGuard();
 *   const live = guard.begin();        // new identity epoch starts
 *   const data = await fetchStuff();
 *   if (!live()) return;               // stale: a newer epoch exists
 *   setState(data);
 */
export function createGenerationGuard() {
  let generation = 0;
  return {
    /* Start a new epoch (invalidates everything older) and return its
     * liveness check. Call at the top of the identity-dependent effect. */
    begin() {
      generation += 1;
      const mine = generation;
      return () => mine === generation;
    },
    /* Liveness check for the CURRENT epoch without starting a new one —
     * for loads triggered inside an epoch (panel refresh, history). */
    watch() {
      const mine = generation;
      return () => mine === generation;
    },
    /* Kill every outstanding check immediately (sign-out does this
     * synchronously, before any network work). */
    invalidate() {
      generation += 1;
    },
  };
}

/* Thread guard — the identity guard's blind spot.
 *
 * createGenerationGuard only knows about WHO you are. A stream started in
 * thread A stays "live" when you click into thread B, because the identity
 * never changed — so A's tokens landed in B's message list. This guards
 * WHERE the answer belongs.
 *
 *   const belongs = createThreadGuard(() => activeIdRef.current, "t-abc");
 *   if (!belongs()) return;   // user moved to another chat: drop the chunk
 *
 * readActive is a getter, not a value: consume() closes over state that is
 * already stale by the time chunks arrive, which is the whole bug.
 */
export function createThreadGuard(readActive, streamThreadId) {
  return () => readActive() === streamThreadId;
}

/* Should this streamed chunk be written to the UI?
 * Pure, so the rule is testable without React or a network. */
export function shouldApplyChunk(live, belongs) {
  return Boolean(live) && Boolean(belongs);
}
