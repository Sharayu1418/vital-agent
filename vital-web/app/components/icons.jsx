/* Inline stroke icons (16px grid, currentColor) so controls inherit theme
 * colors and render identically everywhere, unlike platform emoji. */

const base = {
  width: 16, height: 16, viewBox: "0 0 24 24", fill: "none",
  stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round",
  strokeLinejoin: "round", "aria-hidden": true,
};

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

export function MoonIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
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
