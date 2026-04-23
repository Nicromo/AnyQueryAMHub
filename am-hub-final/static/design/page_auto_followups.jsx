// PageAutoFollowups — автоматические фолоуапы по клиентам

const TRIGGER_TYPES = {
  after_meeting: "После встречи",
  no_activity:   "Нет активности",
  health_drop:   "Падение Health",
  renewal_soon:  "Продление скоро",
  manual:        "Вручную",
};

function FollowupCard({ item, clients, onToggle, onEdit, onDelete }) {
  const [logs, setLogs]   = React.useState(null);
  const [preview, setPreview] = React.useState(null);
  const [previewClient, setPreviewClient] = React.useState("");

  const loadLogs = async () => {
    if (logs !== null) { setLogs(null); return; }
    const r = await fetch(`/api/auto-followups/${item.id}/logs`);
    if (r.ok) setLogs((await r.json()).items || []);
  };

  const testPreview = async () => {
    if (!previewClient) return alert("Выберите клиента для превью");
    const r = await fetch(`/api/auto-followups/${item.id}/test`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: previewClient }),
    });
    if (r.ok) setPreview((await r.json()).preview || "");
    else alert("Ошибка генерации превью");
  };

  return (
    <div style={{ background: "var(--ink-2)", border: `1px solid ${item.is_active ? "var(--ok)" : "var(--line)"}`, borderRadius: 6, padding: 16, marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 8 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 4 }}>{item.name}</div>
          <div style={{ fontSize: 12, color: "var(--ink-5)" }}>
            {TRIGGER_TYPES[item.trigger_type] || item.trigger_type}
            {item.delay_hours ? ` · через ${item.delay_hours}ч` : ""}
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <button onClick={() => onToggle(item.id, !item.is_active)}
            style={{ fontSize: 11, padding: "4px 10px", borderRadius: 4, cursor: "pointer",
              background: item.is_active ? "var(--ok)" : "var(--ink-4)", border: "none", color: "var(--ink-0)", fontWeight: 500 }}>
            {item.is_active ? "✓ Активен" : "Выкл"}
          </button>
          <button onClick={() => onEdit(item)}
            style={{ fontSize: 11, padding: "4px 8px", borderRadius: 3, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)" }}>
            Изм.
          </button>
          <button onClick={() => onDelete(item.id)}
            style={{ fontSize: 11, padding: "4px 8px", borderRadius: 3, cursor: "pointer", background: "transparent", border: "1px solid var(--critical)", color: "var(--critical)" }}>
            Удал.
          </button>
        </div>
      </div>

      {item.message_template && (
        <div style={{ fontSize: 12, color: "var(--ink-6)", background: "var(--ink-3)", borderRadius: 4, padding: "8px 10px", marginBottom: 8, whiteSpace: "pre-wrap", fontFamily: "monospace" }}>
          {item.message_template.slice(0, 200)}{item.message_template.length > 200 ? "…" : ""}
        </div>
      )}

      {/* Превью */}
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}>
        <select value={previewClient} onChange={e => setPreviewClient(e.target.value)}
          style={{ fontSize: 11, background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 3, padding: "3px 6px", color: "var(--ink-8)" }}>
          <option value="">Выберите клиента для превью</option>
          {clients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <button onClick={testPreview} style={{ fontSize: 11, padding: "3px 8px", borderRadius: 3, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)" }}>
          Превью
        </button>
        <button onClick={loadLogs} style={{ fontSize: 11, padding: "3px 8px", borderRadius: 3, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-5)" }}>
          {logs === null ? "История" : "Скрыть"}
        </button>
      </div>

      {preview && (
        <div style={{ fontSize: 12, color: "var(--ink-7)", background: "var(--ink-3)", borderRadius: 4, padding: "10px 12px", marginBottom: 8, borderLeft: "3px solid var(--signal)" }}>
          <div style={{ fontSize: 10, color: "var(--signal)", marginBottom: 4, fontWeight: 600 }}>ПРЕВЬЮ</div>
          {preview}
        </div>
      )}

      {logs !== null && (
        <div style={{ marginTop: 4 }}>
          {logs.length === 0
            ? <div style={{ fontSize: 11, color: "var(--ink-4)" }}>Отправок ещё не было</div>
            : logs.slice(0, 5).map(l => (
              <div key={l.id} style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 3 }}>
                <span style={{ color: l.status === "sent" ? "var(--ok)" : "var(--critical)" }}>
                  {l.status === "sent" ? "✓" : "✗"}
                </span>
                {" "}{l.executed_at ? new Date(l.executed_at).toLocaleString("ru") : "—"}
                {" · "}{clients.find(c => c.id === l.client_id)?.name || l.client_id}
                {l.error ? ` · ${l.error}` : ""}
              </div>
            ))
          }
        </div>
      )}
    </div>
  );
}

function FollowupForm({ clients, initial, onSave, onCancel }) {
  const [form, setForm] = React.useState(initial || {
    name: "", trigger_type: "after_meeting", delay_hours: 24,
    message_template: "", is_active: true,
  });
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const save = async () => {
    if (!form.name.trim()) return alert("Введите название");
    if (!form.message_template.trim()) return alert("Введите шаблон сообщения");
    const method = initial?.id ? "PATCH" : "POST";
    const url    = initial?.id ? `/api/auto-followups/${initial.id}` : "/api/auto-followups";
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) });
    if (r.ok) onSave();
    else alert("Ошибка сохранения");
  };

  return (
    <div style={{ background: "var(--ink-2)", border: "1px solid var(--signal)", borderRadius: 6, padding: 20, marginBottom: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 16 }}>{initial?.id ? "Редактировать фолоуап" : "Новый автофолоуап"}</div>
      <div style={{ display: "grid", gap: 12 }}>
        <div>
          <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Название *</div>
          <input value={form.name} onChange={e => set("name", e.target.value)}
            style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }} />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <div>
            <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Триггер</div>
            <select value={form.trigger_type} onChange={e => set("trigger_type", e.target.value)}
              style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }}>
              {Object.entries(TRIGGER_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div>
            <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Задержка (часов)</div>
            <input type="number" value={form.delay_hours} onChange={e => set("delay_hours", parseInt(e.target.value) || 0)}
              min={0} max={720}
              style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }} />
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>
            Шаблон сообщения * <span style={{ color: "var(--ink-4)" }}>Переменные: {"{client_name} {manager_name} {last_meeting_date} {health_status}"}</span>
          </div>
          <textarea value={form.message_template} onChange={e => set("message_template", e.target.value)} rows={5}
            placeholder="Привет, {client_name}! Напоминаю…"
            style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13, resize: "vertical", fontFamily: "monospace" }} />
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button onClick={onCancel} style={{ padding: "8px 16px", borderRadius: 4, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)", fontSize: 13 }}>Отмена</button>
          <button onClick={save} style={{ padding: "8px 16px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, fontSize: 13 }}>Сохранить</button>
        </div>
      </div>
    </div>
  );
}

function PageAutoFollowups({ clients = [] }) {
  const [items, setItems]      = React.useState([]);
  const [loading, setLoading]  = React.useState(true);
  const [showForm, setShowForm] = React.useState(false);
  const [editItem, setEditItem] = React.useState(null);

  const load = async () => {
    setLoading(true);
    const r = await fetch("/api/auto-followups");
    if (r.ok) setItems((await r.json()).items || []);
    setLoading(false);
  };
  React.useEffect(() => { load(); }, []);

  const toggle = async (id, active) => {
    await fetch(`/api/auto-followups/${id}/toggle`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ is_active: active }) });
    load();
  };

  const del = async (id) => {
    if (!confirm("Удалить автофолоуап?")) return;
    await fetch(`/api/auto-followups/${id}`, { method: "DELETE" });
    load();
  };

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 600 }}>Автофолоуапы</div>
          <div style={{ fontSize: 13, color: "var(--ink-5)" }}>Автоматические сообщения клиентам по триггерам</div>
        </div>
        <button onClick={() => { setEditItem(null); setShowForm(true); }}
          style={{ padding: "9px 16px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, fontSize: 13 }}>
          + Новый фолоуап
        </button>
      </div>

      {showForm && (
        <FollowupForm clients={clients} initial={editItem} onSave={() => { setShowForm(false); load(); }} onCancel={() => setShowForm(false)} />
      )}

      {loading ? <div style={{ color: "var(--ink-5)" }}>Загрузка…</div> : (
        items.length === 0
          ? <div style={{ color: "var(--ink-5)", textAlign: "center", padding: 48 }}>
              Нет автофолоуапов. Создайте первый!
            </div>
          : items.map(item => (
            <FollowupCard key={item.id} item={item} clients={clients}
              onToggle={toggle}
              onEdit={i => { setEditItem(i); setShowForm(true); }}
              onDelete={del} />
          ))
      )}
    </div>
  );
}
window.PageAutoFollowups = PageAutoFollowups;
