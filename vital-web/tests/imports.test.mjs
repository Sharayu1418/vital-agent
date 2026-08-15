/**
 * Relative imports must carry their file extension.
 *
 * WHY THIS EXISTS
 * ---------------
 * `app/lib/api.js` imported "./theme" with no extension. Next's bundler
 * resolves that without complaint, so `next build` was green — but the unit
 * tests run under plain `node --test`, and Node's ESM loader does no
 * extension guessing. It throws ERR_MODULE_NOT_FOUND at import time.
 *
 * That failure is nastier than a normal one. It kills the whole test FILE
 * before a single test runs, so three files reporting one failure each hid
 * twenty-six tests that were never executed. The suite said 78 tests, 3
 * failures. The truth was 104 tests and no idea about 26 of them.
 *
 * The build passing while the tests cannot even load is the same shape as
 * every other bug in this project: two environments disagreeing about the
 * same code, and the more forgiving one being the one we looked at.
 *
 * A lint rule would also catch this. This is a test because the suite is the
 * thing that already runs in CI, and the rule is four lines.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const LIB = new URL("../app/lib/", import.meta.url).pathname;

// import x from "./y"  |  export * from "../z"  — relative specifiers only.
// Bare specifiers like "react" are package names and must NOT have one.
const RELATIVE = /\bfrom\s+["'](\.[^"']*)["']/g;

test("every relative import in app/lib names a real file extension", () => {
  const offenders = [];

  for (const file of readdirSync(LIB).filter((f) => f.endsWith(".js"))) {
    const source = readFileSync(join(LIB, file), "utf8");
    for (const [, specifier] of source.matchAll(RELATIVE)) {
      if (!/\.(js|mjs|cjs|json)$/.test(specifier)) {
        offenders.push(`${file}: "${specifier}"`);
      }
    }
  }

  assert.deepEqual(offenders, [],
    "these resolve under Next's bundler but throw ERR_MODULE_NOT_FOUND " +
    "under `node --test`, taking the whole test file down with them:\n  " +
    offenders.join("\n  "));
});
