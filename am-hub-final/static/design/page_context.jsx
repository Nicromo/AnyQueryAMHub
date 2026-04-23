// PageContext — сохранение и просмотр контекста по клиентам

function ContextSection({ title, items, color, icon }) {
  if (!items || items.length === 0) return null;
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: color || "var(--ink-5)", marginBottom: 6, display: "flex", alignItems: "center", gap: 6 }}>
        <span>{icon}</span>{title.toUpperCase()}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {items.map((item, i) => (
          <div key={i} style={{ fontSize: 13, color: "var(--ink-7)", padding: "6px 10px", background: "var(--ink-3)", borderRadius: 4, borderLeft: `3px solid ${color || "var(--line)"}` }}>
            {typeof item === "string" ? item : JSON.stringify(item)}
          </div>
        ))}
      </div>
    </div>
  );
}

function ClientContextCard({ client }) {
  const [ctx, setCtx]         = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const [regen, setRegen]     = React.useState(false);
  const [open, setOpen]       = React.useState(false);
  const [editMode, setEditMode] = React.useState(false);
  const [editCtx, setEditCtx]   = React.useState(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    const r = await fetch(`/api/clients/${client.id}/context`);
    if (r.ok) setCtx(await r.json());
    setLoading(false);
  }, [client.id]);

  React.useEffect(() => { if (open && !ctx) load(); }, [open, ctx, load]);

  const regenerate = async () => {
    setRegen(true);
    const r = await fetch(`/api/clients/${client.id}/context/regenerate`, { method: "POST" });
    setRegen(false);
    if (r.ok) { setCtx(await r.json()); }
    else alert("Ошибка генерации контекста");
  };

  const startEdit = () => {
    setEditCtx({
      key_facts:   (ctx?.key_facts   || []).join("\n"),
      pain_points: (ctx?.pain_points || []).join("\n"),
      wins:        (ctx?.wins        || []).join("\n"),
      risks:       (ctx?.risks       || []).join("\n"),
      next_steps:  (ctx?.next_steps  || []).join("\n"),
      notes:       ctx?.notes || "",
    });
    setEditMode(true);
  };

  const saveEdit = async () => {
    const toArr = s => s.split("\n").map(l => l.trim()).filter(Boolean);
    const body = {
      key_facts:   toArr(editCtx.key_facts),
      pain_points: toArr(editCtx.pain_points),
      wins:        toArr(editCtx.wins),
      risks:       toArr(editCtx.risks),
      next_steps:  toArr(editCtx.next_steps),
      notes:       editCtx.notes,
    };
    const r = await fetch(`/api/clients/${client.id}/context`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.ok) { setCtx(await r.json()); setEditMode(false); }
    else alert("Ошибка сохранения");
  };

  const hasContent = ctx && (
    ctx.key_facts?.length || ctx.pain_points?.length || ctx.wins?.length ||
    ctx.risks?.length || ctx.next_steps?.length || ctx.notes
  );

  return (
    <div style={{ marginBottom: 8 }}>
      <button onClick={() => setOpen(o => !o)}
        style={{ width: "100%", display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 16px",
          background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: open ? "6px 6px 0 0" : 6,
          cursor: "pointer", color: "var(--ink-8)", fontSize: 13, fontWeight: 500 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span>{client.name}</span>
          {hasContent && <span style={{ fontSize: 10, padding: "2px 6px", background: "var(--ok)", borderRadius: 3, color: "var(--ink-0)" }}>Контекст есть</span>}
        </div>
        <span style={{ color: "var(--ink-5)", fontSize: 11 }}>{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div style={{ background: "var(--ink-1)", border: "1px solid var(--line)", borderTop: "none", borderRadius: "0 0 6px 6px", padding: 16 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 16, justifyContent: "flex-end" }}>
            <button onClick={regenerate} disabled={regen}
              style={{ fontSize: 12, padding: "6px 12px", borderRadius: 4, cursor: "pointer", background: regen ? "var(--ink-4)" : "var(--signal)", border: "none", color: "var(--ink-0)" }}>
              {regen ? "Генерирую…" : "✨ Обновить контекст"}
            </button>
            {ctx && !editMode && (
              <button onClick={startEdit}
                style={{ fontSize: 12, padding: "6px 12px", borderRadius: 4, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)" }}>
                Редактировать
              </button>
            )}
          </div>

          {loading && <div style={{ color: "var(--ink-5)", fontSize: 13 }}>Загрузка…</div>}

          {!loading && !ctx && (
            <div style={{ textAlign: "center", color: "var(--ink-5)", fontSize: 13, padding: 24 }}>
              Контекст ещё не сгенерирован
            </div>
          )}

          {!loading && ctx && !editMode && (
            <>
              <ContextSection title="Ключевые факты" items={ctx.key_facts} color="var(--signal)" icon="ℹ" />
              <ContextSection title="Боли и проблемы" items={ctx.pain_points} color="var(--critical)" icon="⚠" />
              <ContextSection title="Успехи" items={ctx.wins} color="var(--ok)" icon="✓" />
              <ContextSection title="Риски" items={ctx.risks} color="var(--warn)" icon="△" />
              <ContextSection title="Следующие шаги" items={ctx.next_steps} color="var(--signal)" icon="→" />
              {ctx.notes && (
                <div style={{ fontSize: 13, color: "var(--ink-6)", padding: "10px 12px", background: "var(--ink-3)", borderRadius: 4, whiteSpace: "pre-wrap" }}>
                  {ctx.notes}
                </div>
              )}
              {ctx.updated_at && (
                <div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 12, textAlign: "right" }}>
                  Обновлено: {new Date(ctx.updated_at).toLocaleString("ru")}
                </div>
              )}
            </>
          )}

          {editMode && editCtx && (
            <div style={{ display: "grid", gap: 12 }}>
              {[
                ["key_facts",   "Ключевые факты (по одному в строке)"],
                ["pain_points", "Боли и проблемы"],
                ["wins",        "Успехи"],
                ["risks",       "Риски"],
                ["next_steps",  "Следующие шаги"],
              ].map(([k, label]) => (
                <div key={k}>
                  <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>{label}</div>
                  <textarea value={editCtx[k]} onChange={e => setEditCtx(c => ({ ...c, [k]: e.target.value }))} rows={3}
                    style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13, resize: "vertical" }} />
                </div>
              ))}
              <div>
                <div style={{ fontSize: 11, color: "var(--ink-5)", marginBottom: 4 }}>Заметки</div>
                <textarea value={editCtx.notes} onChange={e => setEditCtx(c => ({ ...c, notes: e.target.value }))} rows={3}
                  style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13, resize: "vertical" }} />
              </div>
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                <button onClick={() => setEditMode(false)}
                  style={{ padding: "8px 16px", borderRadius: 4, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)", fontSize: 13 }}>
                  Отмена
                </button>
                <button onClick={saveEdit}
                  style={{ padding: "8px 16px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, fontSize: 13 }}>
                  Сохранить
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PageContext({ clients = [] }) {
  const [search, setSearch] = React.useState("");

  const filtered = clients.filter(c =>
    !search || c.name?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: "0 auto" }}>
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Контекст клиентов</div>
        <div style={{ fontSize: 13, color: "var(--ink-5)" }}>
          Автоматически собранный и структурированный контекст по каждому клиенту
        </div>
      </div>

      <input value={search} onChange={e => setSearch(e.target.value)}
        placeholder="Поиск по клиенту…"
        style={{ width: "100%", background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, padding: "9px 12px", color: "var(--ink-9)", fontSize: 13, marginBottom: 16, boxSizing: "border-box" }} />

      {filtered.length === 0
        ? <div style={{ textAlign: "center", color: "var(--ink-5)", padding: 48 }}>Клиенты не найдены</div>
        : filtered.map(c => <ClientContextCard key={c.id} client={c} />)
      }
    </div>
  );
}
window.PageContext = PageContext;
