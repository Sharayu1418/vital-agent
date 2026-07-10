"use client";

export default function Sidebar({
  threads, activeId, onSelect, onNew, onDelete, open, onClose,
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

        <footer className="sidebar-foot">
          <span className="side-hint">agents, not search</span>
        </footer>
      </aside>
    </>
  );
}
