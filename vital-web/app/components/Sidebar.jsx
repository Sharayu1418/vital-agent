"use client";
import { accountLabel } from "../lib/auth";

export default function Sidebar({
  threads, activeId, onSelect, onNew, onDelete, open, onClose,
  memories, onForget, authReady, authUser, authBusy, onSignIn, onSignOut,
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

        <div className="sidebar-history">
          <span className="sidebar-label">Chats</span>
          <nav className="thread-list" aria-label="Chat history">
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
        </div>

        <section className="sidebar-memory" aria-labelledby="sidebar-memory-title">
          <div className="sidebar-memory-head">
            <h2 id="sidebar-memory-title">What VITAL knows</h2>
            {memories?.length > 0 && (
              <span aria-label={`${memories.length} saved details`}>{memories.length}</span>
            )}
          </div>
          {!memories?.length ? (
            <p className="side-hint">Nothing saved yet.</p>
          ) : (
            <div className="sidebar-memory-list">
              {memories.map((m) => (
                <div className="memory-row" key={m.key}>
                  <span>{m.fact}</span>
                  <button className="thread-del" title="Forget"
                    onClick={() => onForget(m.key)}>×</button>
                </div>
              ))}
            </div>
          )}
        </section>

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
        </div>
      </aside>
    </>
  );
}
