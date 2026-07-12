/* Inline stroke icons (16px grid, currentColor) so controls inherit theme
 * colors and render identically everywhere, unlike platform emoji. */

const base = {
  width: 16, height: 16, viewBox: "0 0 24 24", fill: "none",
  stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round",
  strokeLinejoin: "round", "aria-hidden": true,
};

/* Official Google "G" — multi-color by brand requirement, so it does NOT
 * inherit currentColor. This branded mark is what makes the sign-in button
 * read as a legitimate Google OAuth control rather than a generic button. */
export function GoogleIcon(props) {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true" {...props}>
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

export function MicIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10v1a7 7 0 0 0 14 0v-1" />
      <line x1="12" y1="18" x2="12" y2="22" />
    </svg>
  );
}

export function SpeakerIcon(props) {
  return (
    <svg {...base} {...props}>
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor" stroke="none" />
      <path d="M15 9a4 4 0 0 1 0 6" />
      <path d="M18 6.5a8 8 0 0 1 0 11" />
    </svg>
  );
}

export function StopIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function MenuIcon(props) {
  return (
    <svg {...base} {...props}>
      <line x1="4" y1="7" x2="20" y2="7" />
      <line x1="4" y1="12" x2="20" y2="12" />
      <line x1="4" y1="17" x2="20" y2="17" />
    </svg>
  );
}

/* Opens the live side panel (sleep / plan / what VITAL knows). A framed
 * panel with a highlighted right column — reads as "insights", not a
 * dark-mode toggle (the theme follows daylight, never a manual switch). */
export function PanelRightIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <line x1="15" y1="4" x2="15" y2="20" />
      <line x1="18" y1="9" x2="18" y2="9" />
      <line x1="18" y1="13" x2="18" y2="13" />
    </svg>
  );
}

export function UploadIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 16V4" />
      <path d="m6 9 6-5 6 5" />
      <path d="M4 20h16" />
    </svg>
  );
}

export function ThumbUpIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M7 10v11" />
      <path d="M7 11 11 3a3 3 0 0 1 3 3v3h5a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 17.8 20H4a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1z" />
    </svg>
  );
}

export function ThumbDownIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M17 14V3" />
      <path d="m17 13-4 8a3 3 0 0 1-3-3v-3H5a2 2 0 0 1-2-2.3l1.2-7A2 2 0 0 1 6.2 4H20a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1z" />
    </svg>
  );
}
