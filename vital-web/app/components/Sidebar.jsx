"use client";
import { accountLabel } from "../lib/auth";

export default function Sidebar({
  threads, activeId, onSelect, onNew, onDelete, open, onClose,
  authReady, authUser, authBusy, authError, onSignIn, onSignOut,
}) {
  return (
    <>
      {open && <div className="scrim" onClick={onClose} />}
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="sidebar-head">
          {/* theme follows the time of day; no manual toggle */}
          <span className="wordmark">VITAL<em>.</em></span>
        </div>

        <button className="new-chat" onClick={onNew}>+ New chat</button>

        <nav className="thread-list">
          {threads.map((t) => (
            <div key={t.id}
              className={`thread ${t.id === activeId ? "active" : ""}`}
              onClick={() => onSelect(t.id)}>
              <span className="thread-title">{t.title}</span>
              <button className="thread-del" title="Remove from list"
                onClick={(e) => { e.stopPropagation(); onDelete(t.id); }}>×</button>
            </div>
          ))}
          {threads.length === 0 && (
            <p className="side-hint">Conversations appear here.</p>
          )}
        </nav>

        <div className="account">
          {!authReady ? (
            <span className="side-hint">Checking sign-in…</span>
          ) : authUser ? (
            <>
              <span className="account-name" title="Signed in with Google">
                {accountLabel(authUser)}
              </span>
              <button className="account-btn" disabled={authBusy}
                onClick={onSignOut}>
                {authBusy ? "Signing out…" : "Sign out"}
              </button>
            </>
          ) : (
            <button className="account-btn account-signin" disabled={authBusy}
              onClick={onSignIn}>
              {authBusy ? "Opening Google…" : "Sign in with Google"}
            </button>
          )}
          {authError && <p className="account-error">{authError}</p>}
        </div>

        <footer className="sidebar-foot">
          <span className="side-hint">agents, not search</span>
        </footer>
      </aside>
    </>
  );
}
