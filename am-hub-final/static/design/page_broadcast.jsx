// PageBroadcast — TG-рассылки по клиентам

const BC_STATUS = {
  draft:  { label: "Черновик", tone: "neutral" },
  sent:   { label: "Отправлено", tone: "ok" },
  failed: { label: "Ошибка", tone: "critical" },
};

const BC_TARGET = {
  all:     "Все клиенты",
  segment: "По сегменту",
  manual:  "Вручную",
  churn:   "Риск оттока",
};

function BroadcastCard({ bc, clients, onSend, onEdit, onDelete }) {
  const [sending, setSending] = React.useState(false);
  const [logs, setLogs]       = React.useState(null);

  const send = async () => {
    if (!confirm("Отправить рассылку сейчас?")) return;
    setSending(true);
    const r = await fetch(`/api/broadcasts/${bc.id}/send`, { method: "POST" });
    setSending(false);
    if (r.ok) onSend();
    else alert("Ошибка отправки");
  };

  const loadLogs = async () => {
    if (logs !== null) { setLogs(null); return; }
    const r = await fetch(`/api/broadcasts/${bc.id}/logs`);
    if (r.ok) setLogs((await r.json()).items || []);
  };

  return (
    <div style={{ background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6, padding: 16, marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 8 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 4 }}>{bc.name}</div>
          <div style={{ fontSize: 12, color: "var(--ink-5)" }}>
            {BC_TARGET[bc.target_type] || bc.target_type}
            {bc.target_filter ? ` · ${bc.target_filter}` : ""}
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <button onClick={send} disabled={sending}
            style={{ fontSize: 11, padding: "4px 10px", borderRadius: 4, cursor: "pointer",
              background: sending ? "var(--ink-4)" : "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500 }}>
            {sending ? "…" : "▶ Отправить"}
          </button>
          <button onClick={() => onEdit(bc)}
            style={{ fontSize: 11, padding: "4px 8px", borderRadius: 4, cursor: "pointer",
              background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)" }}>
            Изм.
          </button>
          <button onClick={() => onDelete(bc.id)}
            style={{ fontSize: 11, padding: "4px 8px", borderRadius: 4, cursor: "pointer",
              background: "transparent", border: "1px solid var(--critical)", color: "var(--critical)" }}>
            Удал.
          </button>
        </div>
      </div>
      <div style={{ fontSize: 12, color: "var(--ink-6)", background: "var(--ink-3)", borderRadius: 4, padding: "8px 10px", marginBottom: 8, whiteSpace: "pre-wrap" }}>
        {bc.message_text}
      </div>
      <button onClick={loadLogs}
        style={{ fontSize: 11, color: "var(--ink-5)", background: "none", border: "none", cursor: "pointer", padding: 0 }}>
        {logs === null ? "Показать историю отправок" : "Скрыть историю"}
      </button>
      {logs !== null && (
        <div style={{ marginTop: 8 }}>
          {logs.length === 0
            ? <div style={{ fontSize: 11, color: "var(--ink-4)" }}>Рассылок ещё не было</div>
            : logs.map(l => (
              <div key={l.id} style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>
                <span style={{ color: l.status === "ok" ? "var(--ok)" : "var(--critical)" }}>
                  {l.status === "ok" ? "✓" : "✗"}
                </span>
                {" "}{l.sent_at ? new Date(l.sent_at).toLocaleString("ru") : "—"} · {l.recipients_count || 0} получ. · {l.error || "OK"}
              </div>
            ))
          }
        </div>
      )}
    </div>
  );
}

function BroadcastForm({ clients, initial, onSave, onCancel }) {
  const [form, setForm] = React.useState(initial || {
    name: "", message_text: "", target_type: "all", target_filter: "",
  });
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const save = async () => {
    if (!form.name.trim()) return alert("Введите название");
    if (!form.message_text.trim()) return alert("Введите текст сообщения");
    const method = initial?.id ? "PATCH" : "POST";
    const url    = initial?.id ? `/api/broadcasts/${initial.id}` : "/api/broadcasts";
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) });
    if (r.ok) onSave();
    else alert("Ошибка сохранения");
  };

  return (
    <div style={{ background: "var(--ink-2)", border: "1px solid var(--signal)", borderRadius: 6, padding: 20, marginBottom: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 16 }}>{initial?.id ? "Редактировать рассылку" : "Новая рассылка"}</div>
      <div style={{ display: "grid", gap: 12 }}>
        <div>
          <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Название *</div>
          <input value={form.name} onChange={e => set("name", e.target.value)} placeholder="Название рассылки"
            style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }} />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <div>
            <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Аудитория</div>
            <select value={form.target_type} onChange={e => set("target_type", e.target.value)}
              style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }}>
              {Object.entries(BC_TARGET).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div>
            <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Фильтр / сегмент</div>
            <input value={form.target_filter} onChange={e => set("target_filter", e.target.value)}
              placeholder="premium, enterprise…"
              style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }} />
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Текст сообщения *</div>
          <textarea value={form.message_text} onChange={e => set("message_text", e.target.value)} rows={5}
            placeholder="Текст, который получат клиенты в Telegram"
            style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13, resize: "vertical" }} />
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button onClick={onCancel} style={{ padding: "8px 16px", borderRadius: 4, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)", fontSize: 13 }}>Отмена</button>
          <button onClick={save} style={{ padding: "8px 16px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, fontSize: 13 }}>Сохранить</button>
        </div>
      </div>
    </div>
  );
}

function PageBroadcast({ clients = [] }) {
  const [items, setItems]      = React.useState([]);
  const [loading, setLoading]  = React.useState(true);
  const [showForm, setShowForm] = React.useState(false);
  const [editItem, setEditItem] = React.useState(null);

  const load = async () => {
    setLoading(true);
    const r = await fetch("/api/broadcasts");
    if (r.ok) setItems((await r.json()).items || []);
    setLoading(false);
  };
  React.useEffect(() => { load(); }, []);

  const del = async (id) => {
    if (!confirm("Удалить рассылку?")) return;
    await fetch(`/api/broadcasts/${id}`, { method: "DELETE" });
    load();
  };

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 600 }}>TG-рассылки</div>
          <div style={{ fontSize: 13, color: "var(--ink-5)" }}>Массовые и сегментированные рассылки клиентам через Telegram</div>
        </div>
        <button onClick={() => { setEditItem(null); setShowForm(true); }}
          style={{ padding: "9px 16px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, fontSize: 13 }}>
          + Новая рассылка
        </button>
      </div>
      {showForm && (
        <BroadcastForm clients={clients} initial={editItem} onSave={() => { setShowForm(false); load(); }} onCancel={() => setShowForm(false)} />
      )}
      {loading ? <div style={{ color: "var(--ink-5)" }}>Загрузка…</div> : (
        items.length === 0
          ? <div style={{ color: "var(--ink-5)", textAlign: "center", padding: 48 }}>Рассылок ещё нет</div>
          : items.map(bc => (
            <BroadcastCard key={bc.id} bc={bc} clients={clients}
              onSend={load}
              onEdit={item => { setEditItem(item); setShowForm(true); }}
              onDelete={del} />
          ))
      )}
    </div>
  );
}
window.PageBroadcast = PageBroadcast;
