"use client";
/* OAuth-first gate screens.
 *
 * These render in place of the app shell until sign-in resolves, so no
 * product UI is ever visible behind the Google popup. The login screen is
 * built to feel like VITAL already knows how to care for you: it greets
 * you in the product's own time-of-day voice (the same helper the chat
 * hero uses) and floats in the same sky gradient as the app — sign-in
 * reads as stepping *into* VITAL, not clearing a wall in front of it.
 *
 * The loading screen is the only one that server-renders (auth starts
 * "unknown"), so it holds no time-dependent text — no hydration surprises.
 * The login/unconfigured screens appear only after client auth resolves. */

import { timeGreeting } from "../lib/theme";
import { GoogleIcon } from "./icons";

// What VITAL looks after — quiet proof of breadth, in the product's order.
const PILLARS = ["Sleep", "Energy", "Plans", "People"];

function Aurora() {
  // soft accent halo behind the card, echoing the page's sun/dawn glow
  return <div className="gate-aurora" aria-hidden="true" />;
}

export function LoadingScreen() {
  return (
    <div className="gate">
      <Aurora />
      <div className="gate-card gate-loading">
        <span className="wordmark gate-mark">VITAL<em>.</em></span>
        <span className="gate-spinner" aria-label="Loading" />
      </div>
    </div>
  );
}

export function LoginScreen({ busy, error, onSignIn }) {
  const g = timeGreeting(new Date().getHours());
  return (
    <div className="gate">
      <Aurora />
      <div className="gate-card gate-login">
        <span className="wordmark gate-mark">VITAL<em>.</em></span>

        <div className="gate-welcome">
          <p className="gate-eyebrow">{g.hi}</p>
          <h1 className="gate-headline">{g.line}</h1>
        </div>

        <p className="gate-copy">
          Sign in to keep your chats, plans, sleep data, and activity posts
          synced.
        </p>

        <button className="gate-google" disabled={busy} onClick={onSignIn}>
          <GoogleIcon className="gate-google-mark" />
          <span>{busy ? "Opening Google…" : "Continue with Google"}</span>
        </button>

        {error && <p className="gate-error" role="alert">{error}</p>}
        <p className="gate-reassure">You can sign out anytime.</p>

        <ul className="gate-pillars" aria-label="What VITAL looks after">
          {PILLARS.map((p) => <li key={p}>{p}</li>)}
        </ul>
      </div>
    </div>
  );
}

export function UnconfiguredScreen() {
  return (
    <div className="gate">
      <Aurora />
      <div className="gate-card">
        <span className="wordmark gate-mark">VITAL<em>.</em></span>
        <p className="gate-copy">
          Google sign-in is not configured for this deployment.
        </p>
      </div>
    </div>
  );
}
