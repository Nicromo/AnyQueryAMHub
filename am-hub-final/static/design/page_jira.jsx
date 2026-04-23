// PageJira — интеграция с Jira

function JiraSettingsPanel({ onSaved }) {
  const [cfg, setCfg]     = React.useState({ url: "", email: "", api_token: "" });
  const [saving, setSaving] = React.useState(false);
  const [loaded, setLoaded] = React.useState(false);

  React.useEffect(() => {
    fetch("/api/jira/settings").then(r => r.ok ? r.json() : null).then(d => {
      if (d) setCfg({ url: d.url || "", email: d.email || "", api_token: "" });
      setLoaded(true);
    });
  }, []);

  const save = async () => {
    if (!cfg.url || !cfg.email || !cfg.api_token) return alert("Заполните все поля");
    setSaving(true);
    const r = await fetch("/api/jira/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cfg) });
    setSaving(false);
    if (r.ok) onSaved();
    else alert("Ошибка сохранения");
  };

  if (!loaded) return <div style={{ color: "var(--ink-5)" }}>Загрузка…</div>;

  return (
    <div style={{ background: "var(--ink-2)", border: "1px solid var(--signal)", borderRadius: 6, padding: 20, marginBottom: 20 }}>
      <div style={{ fontWeight: 600, marginBottom: 12 }}>Настройки Jira</div>
      <div style={{ display: "grid", gap: 10 }}>
        <div>
          <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Jira URL</div>
          <input value={cfg.url} onChange={e => setCfg(c => ({ ...c, url: e.target.value }))}
            placeholder="https://your-company.atlassian.net"
            style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }} />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <div>
            <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Email</div>
            <input value={cfg.email} onChange={e => setCfg(c => ({ ...c, email: e.target.value }))}
              style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }} />
          </div>
          <div>
            <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>API Token</div>
            <input type="password" value={cfg.api_token} onChange={e => setCfg(c => ({ ...c, api_token: e.target.value }))}
              placeholder="Оставьте пустым, чтобы не менять"
              style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }} />
          </div>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button onClick={save} disabled={saving}
            style={{ padding: "8px 16px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, fontSize: 13 }}>
            {saving ? "Сохраняю…" : "Сохранить"}
          </button>
        </div>
      </div>
    </div>
  );
}

function JiraIssueRow({ issue, onComment }) {
  const priorityColor = { highest: "var(--critical)", high: "var(--warn)", medium: "var(--signal)", low: "var(--ink-5)", lowest: "var(--ink-4)" };
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
      <span style={{ fontSize: 11, fontFamily: "monospace", color: "var(--signal)", whiteSpace: "nowrap", minWidth: 80 }}>{issue.issue_key}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 2 }}>{issue.title}</div>
        {issue.description && <div style={{ fontSize: 11, color: "var(--ink-5)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{issue.description}</div>}
      </div>
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexShrink: 0 }}>
        <span style={{ fontSize: 11, color: priorityColor[issue.priority] || "var(--ink-5)" }}>{issue.priority || "—"}</span>
        <span style={{ fontSize: 11, padding: "2px 6px", background: "var(--ink-3)", borderRadius: 3 }}>{issue.status || "—"}</span>
        {onComment && (
          <button onClick={() => onComment(issue)}
            style={{ fontSize: 11, padding: "2px 8px", borderRadius: 3, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)" }}>
            Комм.
          </button>
        )}
      </div>
    </div>
  );
}

function PageJira({ clients = [] }) {
  const [configured, setConfigured] = React.useState(false);
  const [showSettings, setShowSettings] = React.useState(false);
  const [jql, setJql]             = React.useState("");
  const [results, setResults]     = React.useState([]);
  const [searching, setSearching] = React.useState(false);
  const [createForm, setCreateForm] = React.useState(null);
  const [commentTarget, setCommentTarget] = React.useState(null);
  const [commentText, setCommentText]     = React.useState("");

  React.useEffect(() => {
    fetch("/api/jira/settings").then(r => {
      if (r.ok) r.json().then(d => setConfigured(!!(d.url && d.email)));
    });
  }, []);

  const search = async () => {
    if (!jql.trim()) return;
    setSearching(true);
    const r = await fetch(`/api/jira/search?jql=${encodeURIComponent(jql)}`);
    if (r.ok) setResults((await r.json()).items || []);
    setSearching(false);
  };

  const createIssue = async () => {
    if (!createForm?.project_key || !createForm?.summary) return alert("Заполните проект и тему");
    const r = await fetch("/api/jira/issues", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(createForm) });
    if (r.ok) { setCreateForm(null); alert("Задача создана"); search(); }
    else alert("Ошибка создания задачи");
  };

  const sendComment = async () => {
    if (!commentText.trim()) return;
    const r = await fetch(`/api/jira/issues/${commentTarget.issue_key}/comment`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ comment: commentText }) });
    if (r.ok) { setCommentTarget(null); setCommentText(""); }
    else alert("Ошибка отправки");
  };

  return (
    <div style={{ padding: 24, maxWidth: 1000, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 600 }}>Jira</div>
          <div style={{ fontSize: 13, color: "var(--ink-5)" }}>Поиск, просмотр и создание задач в Jira</div>
        </div>
        <button onClick={() => setShowSettings(s => !s)}
          style={{ padding: "9px 14px", borderRadius: 4, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)", fontSize: 13 }}>
          ⚙ Настройки
        </button>
      </div>

      {showSettings && (
        <JiraSettingsPanel onSaved={() => { setConfigured(true); setShowSettings(false); }} />
      )}

      {!configured && !showSettings && (
        <div style={{ textAlign: "center", padding: 48, color: "var(--ink-5)" }}>
          Настройте подключение к Jira для начала работы
          <br />
          <button onClick={() => setShowSettings(true)}
            style={{ marginTop: 12, padding: "8px 16px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontSize: 13 }}>
            Настроить
          </button>
        </div>
      )}

      {configured && (
        <>
          {/* Поиск */}
          <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
            <input value={jql} onChange={e => setJql(e.target.value)} onKeyDown={e => e.key === "Enter" && search()}
              placeholder='JQL: project = MY AND status = "In Progress"'
              style={{ flex: 1, background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, padding: "9px 12px", color: "var(--ink-9)", fontSize: 13 }} />
            <button onClick={search} disabled={searching}
              style={{ padding: "9px 16px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, fontSize: 13 }}>
              {searching ? "…" : "Найти"}
            </button>
            <button onClick={() => setCreateForm({ project_key: "", summary: "", description: "", issue_type: "Task" })}
              style={{ padding: "9px 14px", borderRadius: 4, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)", fontSize: 13 }}>
              + Задача
            </button>
          </div>

          {/* Форма создания */}
          {createForm && (
            <div style={{ background: "var(--ink-2)", border: "1px solid var(--signal)", borderRadius: 6, padding: 16, marginBottom: 16 }}>
              <div style={{ fontWeight: 600, marginBottom: 12 }}>Новая задача</div>
              <div style={{ display: "grid", gridTemplateColumns: "120px 1fr 1fr", gap: 8, marginBottom: 8 }}>
                <input value={createForm.project_key} onChange={e => setCreateForm(f => ({ ...f, project_key: e.target.value }))}
                  placeholder="Проект" style={{ background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "7px 10px", color: "var(--ink-9)", fontSize: 13 }} />
                <input value={createForm.summary} onChange={e => setCreateForm(f => ({ ...f, summary: e.target.value }))}
                  placeholder="Тема задачи" style={{ background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "7px 10px", color: "var(--ink-9)", fontSize: 13 }} />
                <select value={createForm.client_id || ""} onChange={e => setCreateForm(f => ({ ...f, client_id: e.target.value || null }))}
                  style={{ background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "7px 10px", color: "var(--ink-9)", fontSize: 13 }}>
                  <option value="">Без клиента</option>
                  {clients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </div>
              <textarea value={createForm.description} onChange={e => setCreateForm(f => ({ ...f, description: e.target.value }))} rows={2}
                placeholder="Описание (необязательно)"
                style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "7px 10px", color: "var(--ink-9)", fontSize: 13, resize: "none", marginBottom: 8 }} />
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                <button onClick={() => setCreateForm(null)} style={{ padding: "7px 14px", borderRadius: 4, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)", fontSize: 13 }}>Отмена</button>
                <button onClick={createIssue} style={{ padding: "7px 14px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, fontSize: 13 }}>Создать</button>
              </div>
            </div>
          )}

          {/* Результаты */}
          {results.length > 0 && (
            <div style={{ background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6, padding: "0 16px" }}>
              {results.map(i => <JiraIssueRow key={i.id || i.issue_key} issue={i} onComment={setCommentTarget} />)}
            </div>
          )}

          {/* Диалог комментария */}
          {commentTarget && (
            <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
              <div style={{ background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 8, padding: 24, width: 480 }}>
                <div style={{ fontWeight: 600, marginBottom: 12 }}>Комментарий к {commentTarget.issue_key}</div>
                <textarea value={commentText} onChange={e => setCommentText(e.target.value)} rows={4}
                  style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13, resize: "vertical", marginBottom: 12 }} />
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                  <button onClick={() => setCommentTarget(null)} style={{ padding: "8px 14px", borderRadius: 4, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)", fontSize: 13 }}>Отмена</button>
                  <button onClick={sendComment} style={{ padding: "8px 14px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, fontSize: 13 }}>Отправить</button>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
window.PageJira = PageJira;
