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
