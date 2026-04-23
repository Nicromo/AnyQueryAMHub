// PageHypotheses — трекер гипотез AM по проектам клиентов

const HYP_STATUS = {
  draft:    { label: "Черновик",   tone: "neutral" },
  testing:  { label: "Тестируется", tone: "signal" },
  proven:   { label: "Доказана",   tone: "ok" },
  rejected: { label: "Отклонена",  tone: "critical" },
  paused:   { label: "Пауза",      tone: "warn" },
};

const HYP_TYPE = {
  ab:      "A/B тест",
  feature: "Фича",
  process: "Процесс",
  pricing: "Ценообразование",
};

function HypothesisCard({ hyp, clients, onStatusChange, onEdit, onDelete }) {
  const st = HYP_STATUS[hyp.status] || { label: hyp.status, tone: "neutral" };
  const client = clients.find(c => c.id === hyp.client_id);
  return (
    <div style={{ background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6, padding: 16, marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 8 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 4 }}>{hyp.title}</div>
          <div style={{ fontSize: 12, color: "var(--ink-5)" }}>
            {client ? client.name : "—"} · {HYP_TYPE[hyp.hypothesis_type] || hyp.hypothesis_type} · {hyp.priority}
          </div>
        </div>
        <span style={{ fontSize: 11, padding: "3px 8px", borderRadius: 4,
          background: `color-mix(in oklch, var(--${st.tone === "neutral" ? "ink-5" : st.tone}) 15%, transparent)`,
          color: `var(--${st.tone === "neutral" ? "ink-6" : st.tone})`, whiteSpace: "nowrap" }}>
          {st.label}
        </span>
      </div>
      {hyp.description && <div style={{ fontSize: 12, color: "var(--ink-6)", marginBottom: 8 }}>{hyp.description}</div>}
      {hyp.metrics && <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 6 }}>📊 {hyp.metrics}</div>}
      {hyp.result && <div style={{ fontSize: 11, padding: "6px 10px", background: "var(--ink-3)", borderRadius: 4, marginBottom: 8 }}>Итог: {hyp.result}</div>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {Object.keys(HYP_STATUS).filter(s => s !== hyp.status).map(s => (
          <button key={s} onClick={() => onStatusChange(hyp.id, s)}
            style={{ fontSize: 11, padding: "3px 8px", borderRadius: 4, cursor: "pointer",
              background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)" }}>
            → {HYP_STATUS[s].label}
          </button>
        ))}
        <button onClick={() => onEdit(hyp)}
          style={{ fontSize: 11, padding: "3px 8px", borderRadius: 4, cursor: "pointer",
            background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)", marginLeft: "auto" }}>
          Изменить
        </button>
        <button onClick={() => onDelete(hyp.id)}
          style={{ fontSize: 11, padding: "3px 8px", borderRadius: 4, cursor: "pointer",
            background: "transparent", border: "1px solid var(--critical)", color: "var(--critical)" }}>
          Удалить
        </button>
      </div>
    </div>
  );
}

function HypothesisForm({ clients, initial, onSave, onCancel }) {
  const [form, setForm] = React.useState(initial || {
    title: "", description: "", hypothesis_type: "ab", status: "draft",
    priority: "medium", metrics: "", expected_impact: "", client_id: "",
    start_date: "", end_date: "",
  });
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const save = async () => {
    if (!form.title.trim()) return alert("Введите название");
    const method = initial?.id ? "PATCH" : "POST";
    const url = initial?.id ? `/api/hypotheses/${initial.id}` : "/api/hypotheses";
    const resp = await fetch(url, { method, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...form, client_id: form.client_id || null }) });
    if (resp.ok) onSave();
    else alert("Ошибка сохранения");
  };
  return (
    <div style={{ background: "var(--ink-2)", border: "1px solid var(--signal)", borderRadius: 6, padding: 20, marginBottom: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 16 }}>{initial?.id ? "Редактировать" : "Новая гипотеза"}</div>
      <div style={{ display: "grid", gap: 12 }}>
        <div>
          <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Название *</div>
          <input value={form.title} onChange={e => set("title", e.target.value)} placeholder="Название гипотезы"
            style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }} />
        </div>
        <div>
          <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Клиент</div>
          <select value={form.client_id} onChange={e => set("client_id", e.target.value)}
            style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }}>
            <option value="">Без клиента</option>
            {clients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
          <div>
            <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Тип</div>
            <select value={form.hypothesis_type} onChange={e => set("hypothesis_type", e.target.value)}
              style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }}>
              {Object.entries(HYP_TYPE).map(([k,v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div>
            <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Приоритет</div>
            <select value={form.priority} onChange={e => set("priority", e.target.value)}
              style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }}>
              <option value="low">Низкий</option>
              <option value="medium">Средний</option>
              <option value="high">Высокий</option>
            </select>
          </div>
          <div>
            <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Статус</div>
            <select value={form.status} onChange={e => set("status", e.target.value)}
              style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }}>
              {Object.entries(HYP_STATUS).map(([k,v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Описание</div>
          <textarea value={form.description} onChange={e => set("description", e.target.value)} rows={3}
            style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13, resize: "vertical" }} />
        </div>
        <div>
          <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Метрики успеха</div>
          <input value={form.metrics} onChange={e => set("metrics", e.target.value)}
            placeholder="Как измеряем результат"
            style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }} />
        </div>
        <div>
          <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Ожидаемый эффект</div>
          <input value={form.expected_impact} onChange={e => set("expected_impact", e.target.value)}
            placeholder="Что ожидаем получить"
            style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13 }} />
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button onClick={onCancel} style={{ padding: "8px 16px", borderRadius: 4, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)", fontSize: 13 }}>Отмена</button>
          <button onClick={save} style={{ padding: "8px 16px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, fontSize: 13 }}>Сохранить</button>
        </div>
      </div>
    </div>
  );
}

function PageHypotheses({ clients = [] }) {
  const [hyps, setHyps] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [showForm, setShowForm] = React.useState(false);
  const [editItem, setEditItem] = React.useState(null);
  const [filterStatus, setFilterStatus] = React.useState("");
  const [filterClient, setFilterClient] = React.useState("");

  const load = async () => {
    setLoading(true);
    const params = new URLSearchParams();
    if (filterStatus) params.set("status", filterStatus);
    if (filterClient) params.set("client_id", filterClient);
    const r = await fetch("/api/hypotheses?" + params);
    if (r.ok) setHyps((await r.json()).items || []);
    setLoading(false);
  };
  React.useEffect(() => { load(); }, [filterStatus, filterClient]);

  const changeStatus = async (id, status) => {
    await fetch(`/api/hypotheses/${id}/status`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }) });
    load();
  };
  const del = async (id) => {
    if (!confirm("Удалить гипотезу?")) return;
    await fetch(`/api/hypotheses/${id}`, { method: "DELETE" });
    load();
  };

  const byStatus = Object.keys(HYP_STATUS).reduce((acc, s) => {
    acc[s] = hyps.filter(h => h.status === s);
    return acc;
  }, {});

  return (
    <div style={{ padding: 24, maxWidth: 1000, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 600 }}>Гипотезы</div>
          <div style={{ fontSize: 13, color: "var(--ink-5)" }}>A/B тесты, идеи и эксперименты по клиентам</div>
        </div>
        <button onClick={() => { setEditItem(null); setShowForm(true); }}
          style={{ padding: "9px 16px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, fontSize: 13 }}>
          + Новая гипотеза
        </button>
      </div>

      {/* Фильтры */}
      <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
        <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)}
          style={{ background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, padding: "7px 10px", color: "var(--ink-8)", fontSize: 12 }}>
          <option value="">Все статусы</option>
          {Object.entries(HYP_STATUS).map(([k,v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <select value={filterClient} onChange={e => setFilterClient(e.target.value)}
          style={{ background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, padding: "7px 10px", color: "var(--ink-8)", fontSize: 12 }}>
          <option value="">Все клиенты</option>
          {clients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>

      {showForm && (
        <HypothesisForm clients={clients} initial={editItem} onSave={() => { setShowForm(false); load(); }} onCancel={() => setShowForm(false)} />
      )}

      {loading ? <div style={{ color: "var(--ink-5)" }}>Загрузка…</div> : (
        hyps.length === 0
          ? <div style={{ color: "var(--ink-5)", textAlign: "center", padding: 48 }}>Гипотезы не найдены</div>
          : hyps.map(h => (
            <HypothesisCard key={h.id} hyp={h} clients={clients}
              onStatusChange={changeStatus}
              onEdit={hyp => { setEditItem(hyp); setShowForm(true); }}
              onDelete={del} />
          ))
      )}
    </div>
  );
}
window.PageHypotheses = PageHypotheses;
