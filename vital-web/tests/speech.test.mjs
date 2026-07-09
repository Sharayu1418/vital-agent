import test from "node:test";
import assert from "node:assert/strict";

import {
  getRecognitionCtor, isSynthesisSupported, joinTranscript,
  recognitionErrorText, stripMarkdownForSpeech,
} from "../app/lib/speech.js";

// ---- feature detection (mic disabled when unsupported) ----

test("no window (SSR / node) → no recognition ctor → mic disabled", () => {
  assert.equal(getRecognitionCtor(), null);
  assert.equal(getRecognitionCtor(undefined), null);
});

test("browser without any speech API → null", () => {
  assert.equal(getRecognitionCtor({}), null);
});

test("prefixed webkitSpeechRecognition is found (Chrome/Safari)", () => {
  class Webkit {}
  assert.equal(getRecognitionCtor({ webkitSpeechRecognition: Webkit }), Webkit);
});

test("standard SpeechRecognition wins over prefixed", () => {
  class Std {}
  class Webkit {}
  assert.equal(
    getRecognitionCtor({ SpeechRecognition: Std, webkitSpeechRecognition: Webkit }),
    Std,
  );
});

test("synthesis support needs both speechSynthesis and the utterance ctor", () => {
  assert.equal(isSynthesisSupported(), false);              // node: no window
  assert.equal(isSynthesisSupported({}), false);
  assert.equal(isSynthesisSupported({ speechSynthesis: {} }), false);
  assert.equal(isSynthesisSupported({
    speechSynthesis: {}, SpeechSynthesisUtterance: function U() {},
  }), true);
});

// ---- transcript splicing ----

test("transcript appends after typed text with a single space", () => {
  assert.equal(joinTranscript("I slept", "four hours"), "I slept four hours");
  assert.equal(joinTranscript("I slept ", " four hours "), "I slept four hours");
});

test("transcript with no typed text stands alone", () => {
  assert.equal(joinTranscript("", "hello there"), "hello there");
  assert.equal(joinTranscript(undefined, " hello "), "hello");
});

test("empty transcript leaves typed text unchanged (minus trailing space)", () => {
  assert.equal(joinTranscript("typed", ""), "typed");
  assert.equal(joinTranscript("typed  ", undefined), "typed");
});

// ---- recognition error copy ----

test("permission denied maps to a permissions hint", () => {
  assert.match(recognitionErrorText("not-allowed"), /denied/);
  assert.match(recognitionErrorText("service-not-allowed"), /denied/);
});

test("no-speech and audio-capture have specific copy", () => {
  assert.match(recognitionErrorText("no-speech"), /No speech/);
  assert.match(recognitionErrorText("audio-capture"), /No microphone/);
});

test("user-initiated abort is not surfaced as an error", () => {
  assert.equal(recognitionErrorText("aborted"), null);
});

test("unknown codes fall back to generic copy", () => {
  assert.ok(recognitionErrorText("language-not-supported"));
});

// ---- markdown → speech text ----

test("bold, italics and inline code lose their markers", () => {
  assert.equal(
    stripMarkdownForSpeech("You got **7.5 hours** — that's *solid*, aim for `480` min."),
    "You got 7.5 hours — that's solid, aim for 480 min.",
  );
});

test("links speak their text, not the URL", () => {
  assert.equal(
    stripMarkdownForSpeech("Try [this pool](https://example.com/pool?a=1) nearby."),
    "Try this pool nearby.",
  );
});

test("headings and list markers are dropped, content kept", () => {
  const md = "## Tonight\n- wind down at 10\n- no screens\n1. water\n2) stretch";
  assert.equal(stripMarkdownForSpeech(md), "Tonight\nwind down at 10\nno screens\nwater\nstretch");
});

test("code blocks are summarized, not read character by character", () => {
  const out = stripMarkdownForSpeech("Run this:\n```js\nconst x = 1;\n```\nthen rest.");
  assert.ok(out.includes("code snippet omitted"));
  assert.ok(!out.includes("const x"));
});

test("blockquotes, tables and stray html are flattened", () => {
  assert.equal(stripMarkdownForSpeech("> rest is progress"), "rest is progress");
  assert.equal(stripMarkdownForSpeech("| day | hours |"), "day hours");
  assert.equal(stripMarkdownForSpeech("sleep <br> more"), "sleep more");
});

test("empty and nullish input is safe", () => {
  assert.equal(stripMarkdownForSpeech(""), "");
  assert.equal(stripMarkdownForSpeech(null), "");
  assert.equal(stripMarkdownForSpeech(undefined), "");
});
