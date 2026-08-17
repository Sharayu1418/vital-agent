/**
 * Sizing for the chat composer.
 *
 * THE BUG
 * -------
 * The composer was `<textarea rows={1}>` with `max-height: 160px` in CSS and
 * nothing that ever set its height. rows=1 is not a minimum, it is the size.
 * So the box stayed one line tall forever, and typing past the first line
 * scrolled the earlier words out of sight — you could only ever see the tail
 * of your own sentence. The max-height was never reached because the height
 * never changed, which is why the CSS looks like the feature already existed.
 *
 * Kept here rather than inline in the component because this file's tests run
 * under plain `node --test` with no DOM. A pure function over {style,
 * scrollHeight} can be tested; a useEffect cannot.
 */

/** Tallest the composer may get before it scrolls internally. Matches
 *  max-height in globals.css — both exist because the CSS cap stops a
 *  paste of 500 lines mid-render, and this one stops us asking for it. */
export const MAX_COMPOSER_PX = 160;

/**
 * Resize a textarea to fit its content, up to a cap.
 *
 * @param {{style: {height: string, overflowY: string}, scrollHeight: number}} el
 * @param {number} maxPx
 * @returns {number} the height applied, in px
 */
export function growToFit(el, maxPx = MAX_COMPOSER_PX) {
  if (!el) return 0;

  // Reset FIRST. scrollHeight never shrinks below the element's current
  // height, so measuring without this makes the box a one-way ratchet: it
  // grows as you type and stays tall after you delete everything.
  el.style.height = "auto";

  const wanted = el.scrollHeight;
  const height = Math.min(wanted, maxPx);
  el.style.height = `${height}px`;

  // Only scroll once it has stopped growing. Leaving overflow on permanently
  // shows a scrollbar on a single-line box in some browsers.
  el.style.overflowY = wanted > maxPx ? "auto" : "hidden";
  return height;
}

/** The server rejects anything longer (api.py: max_length=2000). Enforced in
 *  the browser too, so an over-long message is visibly capped as you type
 *  rather than failing with a 422 the user never sees a reason for. */
export const MAX_MESSAGE_CHARS = 2000;

/** Show a count only when it starts to matter — a permanent counter on an
 *  empty box is noise, and a limit you learn about by hitting it is worse. */
export function charCountNote(text, max = MAX_MESSAGE_CHARS) {
  const used = (text || "").length;
  if (used < max - 200) return null;
  return `${used} / ${max}`;
}
