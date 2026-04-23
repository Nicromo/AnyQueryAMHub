// PageGDrive — интеграция с Google Drive

function DriveFileCard({ file, clients, onLink }) {
  const [linking, setLinking] = React.useState(false);
  const [clientId, setClientId] = React.useState("");

  const link = async () => {
    if (!clientId) return alert("Выберите клиента");
    setLinking(true);
    const r = await fetch(`/api/clients/${clientId}/gdrive/link`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_id: file.id, file_name: file.name, mime_type: file.mimeType, web_url: file.webViewLink }),
    });
    setLinking(false);
    if (r.ok) { setClientId(""); onLink(); }
    else alert("Ошибка привязки");
  };

  const icon = file.mimeType?.includes("spreadsheet") ? "📊"
    : file.mimeType?.includes("document") ? "📄"
    : file.mimeType?.includes("presentation") ? "📊"
    : file.mimeType?.includes("folder") ? "📁"
    : "📎";

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
      <span style={{ fontSize: 18 }}>{icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <a href={file.webViewLink} target="_blank" rel="noopener noreferrer"
          style={{ fontSize: 13, fontWeight: 500, color: "var(--signal)", textDecoration: "none" }}>
          {file.name}
        </a>
        <div style={{ fontSize: 11, color: "var(--ink-5)" }}>
          {file.modifiedTime ? new Date(file.modifiedTime).toLocaleDateString("ru") : "—"}
          {file.size ? ` · ${Math.round(file.size / 1024)} KB` : ""}
        </div>
      </div>
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexShrink: 0 }}>
        <select value={clientId} onChange={e => setClientId(e.target.value)}
          style={{ fontSize: 11, background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "4px 6px", color: "var(--ink-8)" }}>
          <option value="">Привязать к…</option>
          {clients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <button onClick={link} disabled={linking || !clientId}
          style={{ fontSize: 11, padding: "4px 8px", borderRadius: 3, cursor: "pointer",
            background: clientId ? "var(--signal)" : "var(--ink-4)", border: "none", color: "var(--ink-0)" }}>
          {linking ? "…" : "Привязать"}
        </button>
      </div>
    </div>
  );
}

function ClientFilesSection({ client, onUnlink }) {
  const [files, setFiles] = React.useState([]);
  const [open, setOpen]   = React.useState(false);

  const load = React.useCallback(() => {
    fetch(`/api/clients/${client.id}/gdrive`).then(r => r.ok ? r.json() : null).then(d => {
      if (d) setFiles(d.items || []);
    });
  }, [client.id]);

  React.useEffect(() => { if (open) load(); }, [open, load]);

  const unlink = async (fileId) => {
    if (!confirm("Удалить привязку файла?")) return;
    const r = await fetch(`/api/clients/${client.id}/gdrive/${fileId}`, { method: "DELETE" });
    if (r.ok) { load(); onUnlink?.(); }
  };

  return (
    <div style={{ marginBottom: 8 }}>
      <button onClick={() => setOpen(o => !o)}
        style={{ width: "100%", display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 14px",
          background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 5, cursor: "pointer", color: "var(--ink-8)", fontSize: 13, fontWeight: 500 }}>
        <span>{client.name}</span>
        <span style={{ color: "var(--ink-5)", fontSize: 11 }}>{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div style={{ background: "var(--ink-1)", border: "1px solid var(--line)", borderTop: "none", borderRadius: "0 0 5px 5px", padding: "0 14px" }}>
          {files.length === 0
            ? <div style={{ fontSize: 12, color: "var(--ink-4)", padding: "12px 0" }}>Нет привязанных файлов</div>
            : files.map(f => (
              <div key={f.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
                <span style={{ fontSize: 16 }}>📎</span>
                <a href={f.web_url || "#"} target="_blank" rel="noopener noreferrer"
                  style={{ flex: 1, fontSize: 13, color: "var(--signal)", textDecoration: "none" }}>{f.file_name}</a>
                <span style={{ fontSize: 11, color: "var(--ink-5)" }}>
                  {f.linked_at ? new Date(f.linked_at).toLocaleDateString("ru") : ""}
                </span>
                <button onClick={() => unlink(f.id)}
                  style={{ fontSize: 11, padding: "2px 8px", borderRadius: 3, cursor: "pointer", background: "transparent", border: "1px solid var(--critical)", color: "var(--critical)" }}>
                  Удал.
                </button>
              </div>
            ))
          }
        </div>
      )}
    </div>
  );
}

function PageGDrive({ clients = [] }) {
  const [tab, setTab]       = React.useState("browse");
  const [query, setQuery]   = React.useState("");
  const [files, setFiles]   = React.useState([]);
  const [loading, setLoading] = React.useState(false);
  const [authUrl, setAuthUrl] = React.useState(null);
  const [linked, setLinked]   = React.useState(0);

  React.useEffect(() => {
    fetch("/api/gdrive/auth").then(r => r.ok ? r.json() : null).then(d => {
      if (d?.auth_url) setAuthUrl(d.auth_url);
      else { setAuthUrl(null); loadFiles(""); }
    });
  }, []);

  const loadFiles = async (q) => {
    setLoading(true);
    const r = await fetch(`/api/gdrive/files?query=${encodeURIComponent(q || "")}`);
    if (r.ok) setFiles((await r.json()).items || []);
    setLoading(false);
  };

  const search = () => loadFiles(query);

  if (authUrl) {
    return (
      <div style={{ padding: 24, maxWidth: 600, margin: "0 auto", textAlign: "center" }}>
        <div style={{ fontSize: 22, fontWeight: 600, marginBottom: 12 }}>Google Drive</div>
        <div style={{ color: "var(--ink-5)", marginBottom: 24 }}>Для доступа к Google Drive необходима авторизация через Google</div>
        <a href={authUrl} target="_blank" rel="noopener noreferrer"
          style={{ display: "inline-block", padding: "10px 24px", background: "var(--signal)", borderRadius: 6, color: "var(--ink-0)", textDecoration: "none", fontWeight: 500 }}>
          Авторизоваться через Google
        </a>
        <div style={{ marginTop: 16, fontSize: 12, color: "var(--ink-4)" }}>
          После авторизации перезагрузите эту страницу
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: 24, maxWidth: 1000, margin: "0 auto" }}>
      <div style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Google Drive</div>
      <div style={{ fontSize: 13, color: "var(--ink-5)", marginBottom: 20 }}>Файлы из Google Drive с привязкой к клиентам</div>

      {/* Табы */}
      <div style={{ display: "flex", gap: 2, marginBottom: 20, borderBottom: "1px solid var(--line)", paddingBottom: 0 }}>
        {[["browse", "Обзор Drive"], ["clients", "По клиентам"]].map(([k, v]) => (
          <button key={k} onClick={() => setTab(k)}
            style={{ padding: "8px 16px", fontSize: 13, cursor: "pointer", background: "none", border: "none",
              borderBottom: tab === k ? "2px solid var(--signal)" : "2px solid transparent",
              color: tab === k ? "var(--signal)" : "var(--ink-6)", fontWeight: tab === k ? 600 : 400 }}>
            {v}
          </button>
        ))}
      </div>

      {tab === "browse" && (
        <>
          <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
            <input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => e.key === "Enter" && search()}
              placeholder="Поиск по файлам…"
              style={{ flex: 1, background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, padding: "9px 12px", color: "var(--ink-9)", fontSize: 13 }} />
            <button onClick={search} disabled={loading}
              style={{ padding: "9px 16px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, fontSize: 13 }}>
              {loading ? "…" : "Найти"}
            </button>
          </div>
          {loading ? <div style={{ color: "var(--ink-5)" }}>Загрузка…</div>
            : files.length === 0
              ? <div style={{ color: "var(--ink-5)", textAlign: "center", padding: 32 }}>Введите запрос для поиска файлов</div>
              : (
                <div style={{ background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6, padding: "0 16px" }}>
                  {files.map(f => <DriveFileCard key={f.id} file={f} clients={clients} onLink={() => setLinked(n => n + 1)} />)}
                </div>
              )
          }
        </>
      )}

      {tab === "clients" && (
        <div>
          {clients.length === 0
            ? <div style={{ color: "var(--ink-5)", textAlign: "center", padding: 32 }}>Нет клиентов</div>
            : clients.map(c => <ClientFilesSection key={c.id} client={c} onUnlink={() => setLinked(n => n + 1)} />)
          }
        </div>
      )}
    </div>
  );
}
window.PageGDrive = PageGDrive;
