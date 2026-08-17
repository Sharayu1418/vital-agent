/**
 * Composer sizing.
 *
 * The bug: the chat box was `<textarea rows={1}>` with a max-height in CSS
 * and nothing that ever set a height. rows=1 is the size, not a minimum, so
 * the box stayed one line tall and typing past it pushed the start of your
 * own sentence out of view. Reported as "longer sentences don't show, it
 * cuts sentences down".
 *
 * The CSS made it look solved — a max-height of 160px reads like a box that
 * grows to 160px. It never grew, so the cap never applied.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  MAX_COMPOSER_PX, MAX_MESSAGE_CHARS, charCountNote, growToFit,
} from "../app/lib/composer.js";

/** Stand-in for a textarea. scrollHeight is the height the CONTENT wants,
 *  which is what the browser reports once height is reset to auto. */
function fakeTextarea(contentHeight) {
  return { style: { height: "", overflowY: "" }, scrollHeight: contentHeight };
}

test("a single line stays at the height of one line", () => {
  const el = fakeTextarea(38);
  assert.equal(growToFit(el), 38);
  assert.equal(el.style.height, "38px");
});

test("the box grows as the sentence gets longer", () => {
  const one = fakeTextarea(38);
  const three = fakeTextarea(96);
  growToFit(one);
  growToFit(three);
  assert.ok(Number.parseInt(three.style.height, 10)
            > Number.parseInt(one.style.height, 10),
    "a three-line message must be taller than a one-line message — this is " +
    "the whole bug");
});

test("growth stops at the cap and scrolls from there", () => {
  const el = fakeTextarea(900);          // a pasted essay
  assert.equal(growToFit(el), MAX_COMPOSER_PX);
  assert.equal(el.style.overflowY, "auto");
});

test("a box that fits does not show a scrollbar", () => {
  const el = fakeTextarea(38);
  growToFit(el);
  assert.equal(el.style.overflowY, "hidden");
});

test("height is reset before measuring, so deleting text shrinks the box", () => {
  // scrollHeight never reports less than the element's current height, so
  // without the reset the box is a one-way ratchet: tall forever after one
  // long message. This asserts the reset happens by checking the order of
  // writes to style.height.
  const writes = [];
  const el = {
    scrollHeight: 38,
    style: {
      overflowY: "",
      set height(v) { writes.push(v); },
      get height() { return writes[writes.length - 1] ?? ""; },
    },
  };
  growToFit(el);
  assert.equal(writes[0], "auto", "must clear the height before measuring");
  assert.equal(writes[1], "38px");
});

test("a missing element is survivable", () => {
  assert.equal(growToFit(null), 0);      // the ref before mount
});

test("the JS cap and the CSS cap are the same number", () => {
  // Two places enforce this, so they can disagree. If the CSS is lower the
  // box is clipped below where JS thinks it stopped; if higher, the cap is
  // decorative.
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  const block = css.match(/\.composer textarea[^}]*}/s)[0];
  const declared = Number.parseInt(block.match(/max-height:\s*(\d+)px/)[1], 10);
  assert.equal(declared, MAX_COMPOSER_PX);
});

test("the character counter appears only near the limit", () => {
  assert.equal(charCountNote(""), null);
  assert.equal(charCountNote("a".repeat(500)), null);
  assert.equal(charCountNote("a".repeat(MAX_MESSAGE_CHARS - 199)),
               `${MAX_MESSAGE_CHARS - 199} / ${MAX_MESSAGE_CHARS}`);
});

test("the browser limit matches what the server will accept", () => {
  // api.py declares message max_length=2000. If the textarea allowed more,
  // an over-long message would fail as a 422 with nothing on screen
  // explaining why — which is the same complaint as the one that started
  // this, arriving by a different route.
  const api = readFileSync(
    new URL("../../vital-app/src/vital/api.py", import.meta.url), "utf8");
  const declared = Number.parseInt(
    api.match(/message:\s*str\s*=\s*Field\(min_length=1,\s*max_length=(\d+)\)/)[1], 10);
  assert.equal(declared, MAX_MESSAGE_CHARS);
});
