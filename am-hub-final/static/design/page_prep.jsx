// page_prep.jsx — Подготовка к встрече (одноэкранный брифинг по клиенту)

function HealthBar({ score }) {
  if (score === null || score === undefined) return null;
  const pct  = Math.round(score * 100);
  const tone = pct >= 80 ? "#4ade80" : pct >= 55 ? "#facc15" : "#f87171";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <div style={{ flex: 1, height: 6, background: "var(--ink-3)", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: tone, borderRadius: 3, transition: "width 0.5s" }} />
      </div>
      <span style={{ fontSize: 13, fontWeight: 600, color: tone, minWidth: 36, textAlign: "right" }}>{pct}%</span>
    </div>
  );
}

function PrepSection({ title, icon, children, accent }) {
  return (
    <div style={{ background: "var(--ink-2)", border: `1px solid ${accent || "var(--line)"}`, borderRadius: 8, padding: 16, marginBottom: 12 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: accent || "var(--ink-5)", marginBottom: 10, textTransform: "uppercase", letterSpacing: "0.08em" }}>
        {icon} {title}
      </div>
      {children}
    </div>
  );
}

function PagePrep() {
  const params    = new URLSearchParams(window.location.search);
  const clientId  = params.get("client_id");

  const [data, setData]         = React.useState(null);
  const [loading, setLoading]   = React.useState(true);
  const [error, setError]       = React.useState(null);
  const [briefText, setBriefText] = React.useState(null);
  const [briefLoading, setBriefLoading] = React.useState(false);
  const [sendingFollowup, setSendingFollowup] = React.useState(false);
  const [note, setNote]         = React.useState("");
  const [noteSaved, setNoteSaved] = React.useState(false);

  React.useEffect(() => {
    if (!clientId) { setError("Не указан client_id в URL"); setLoading(false); return; }
    fetch(`/api/clients/${clientId}/prep-brief`, { credentials: "include" })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [clientId]);

  const loadTransferBrief = async () => {
    setBriefLoading(true);
    const r = await fetch(`/api/clients/${clientId}/transfer-brief`, { credentials: "include" });
    setBriefLoading(false);
    if (r.ok) setBriefText((await r.json()).text || "");
    else setBriefText("Ошибка генерации брифинга");
  };

  const sendFollowup = async () => {
    setSendingFollowup(true);
    const r = await fetch(`/api/clients/${clientId}/quick-followup`, { method: "POST", credentials: "include" });
    setSendingFollowup(false);
    const d = r.ok ? await r.json() : null;
    if (d?.ok) alert("Фолоуап отправлен в Telegram");
    else alert(d?.error || "Ошибка отправки");
  };

  const saveNote = async () => {
    if (!note.trim()) return;
    // Сохраняем как внутреннюю заметку встречи через AI summary endpoint
    const r = await fetch(`/api/clients/${clientId}/context`, {
      method: "PATCH", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes: note }),
    });
    if (r.ok) { setNoteSaved(true); setTimeout(() => setNoteSaved(false), 3000); }
    else alert("Ошибка сохранения заметки");
  };

  if (!clientId) return (
    <div style={{ padding: 40, textAlign: "center", color: "var(--ink-5)" }}>
      Укажите client_id в URL: <code>/design/prep?client_id=123</code>
    </div>
  );

  if (loading) return (
    <div style={{ padding: 40, textAlign: "center", color: "var(--ink-5)" }}>Загружаю данные…</div>
  );

  if (error) return (
    <div style={{ padding: 40, textAlign: "center", color: "var(--critical)" }}>Ошибка: {error}</div>
  );

  const { client, meetings, tasks, context, nps, contacts } = data;
  const overdueTasks = tasks.filter(t => t.overdue);
  const openTasks    = tasks.filter(t => !t.overdue);

  const statusLabel = { risk: "🔴 Риск", warn: "🟡 Внимание", ok: "🟢 Ок" };
  const renewalText = client.renewal_days !== null
    ? (client.renewal_days < 0 ? `❗ Просрочено (${-client.renewal_days}д назад)` : `📅 Через ${client.renewal_days}д`)
    : null;

  return (
    <div style={{ padding: "24px 28px", maxWidth: 1100, margin: "0 auto" }}>

      {/* Шапка */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 24 }}>
        <div>
          <button onClick={() => history.back()}
            style={{ fontSize: 12, color: "var(--ink-5)", background: "none", border: "none", cursor: "pointer", padding: 0, marginBottom: 8 }}>
            ← Назад
          </button>
          <div style={{ fontSize: 24, fontWeight: 700, marginBottom: 4 }}>{client.name}</div>
          <div style={{ fontSize: 13, color: "var(--ink-5)", display: "flex", gap: 16 }}>
            <span>{client.segment || "—"}</span>
            <span>{statusLabel[client.status] || client.status}</span>
            {renewalText && <span style={{ color: client.renewal_days !== null && client.renewal_days < 14 ? "var(--critical)" : "var(--ink-5)" }}>{renewalText}</span>}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={sendFollowup} disabled={sendingFollowup}
            style={{ padding: "9px 16px", borderRadius: 6, cursor: "pointer", background: sendingFollowup ? "var(--ink-4)" : "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 600, fontSize: 13 }}>
            {sendingFollowup ? "Отправляю…" : "📤 Быстрый фолоуап"}
          </button>
          <a href={`/design/clients?id=${clientId}`}
            style={{ padding: "9px 16px", borderRadius: 6, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-7)", fontSize: 13, textDecoration: "none" }}>
            Карточка клиента →
          </a>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>

        {/* Левая колонка */}
        <div>

          {/* Health */}
          <PrepSection title="Health Score" icon="❤" accent={client.health_score >= 0.8 ? "var(--ok)" : client.health_score >= 0.55 ? "var(--warn)" : "var(--critical)"}>
            <HealthBar score={client.health_score} />
            {client.churn_risk !== null && (
              <div style={{ fontSize: 12, color: "var(--ink-5)", marginTop: 8 }}>
                Риск оттока: <b style={{ color: client.churn_risk > 0.5 ? "var(--critical)" : "var(--ink-7)" }}>{Math.round(client.churn_risk * 100)}%</b>
              </div>
            )}
          </PrepSection>

          {/* Последние встречи */}
          <PrepSection title="Последние встречи" icon="📅" accent="var(--signal)">
            {meetings.length === 0
              ? <div style={{ fontSize: 12, color: "var(--ink-4)" }}>Встреч не было</div>
              : meetings.map((m, i) => (
                <div key={i} style={{ display: "flex", gap: 10, padding: "8px 0", borderBottom: i < meetings.length - 1 ? "1px solid var(--line)" : "none" }}>
                  <span style={{ fontSize: 11, color: "var(--ink-5)", whiteSpace: "nowrap", minWidth: 90 }}>{m.date}</span>
                  <div>
                    <span style={{ fontSize: 11, background: "var(--ink-3)", borderRadius: 3, padding: "2px 6px", marginRight: 6 }}>{m.type}</span>
                    <span style={{ fontSize: 12, color: "var(--ink-7)" }}>{m.summary || "—"}</span>
                  </div>
                </div>
              ))
            }
          </PrepSection>

          {/* NPS */}
          {nps && (
            <PrepSection title="Последний NPS" icon="⭐" accent={nps.score >= 9 ? "var(--ok)" : nps.score >= 7 ? "var(--warn)" : "var(--critical)"}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ fontSize: 28, fontWeight: 700, color: nps.score >= 9 ? "var(--ok)" : nps.score >= 7 ? "var(--warn)" : "var(--critical)" }}>{nps.score}</span>
                <div>
                  <div style={{ fontSize: 12, color: "var(--ink-5)" }}>{nps.date}</div>
                  {nps.comment && <div style={{ fontSize: 13, color: "var(--ink-7)", marginTop: 4 }}>«{nps.comment}»</div>}
                </div>
              </div>
            </PrepSection>
          )}

          {/* Контакты */}
          {contacts.length > 0 && (
            <PrepSection title="Контакты" icon="👤" accent="var(--ink-4)">
              {contacts.map((c, i) => (
                <div key={i} style={{ fontSize: 12, color: "var(--ink-7)", marginBottom: 6 }}>
                  <b>{c.name}</b>
                  {c.role && <span style={{ color: "var(--ink-5)", marginLeft: 8 }}>{c.role}</span>}
                  {c.email && <div style={{ color: "var(--signal)", marginTop: 2 }}>{c.email}</div>}
                  {c.phone && <div style={{ color: "var(--ink-5)", marginTop: 2 }}>{c.phone}</div>}
                </div>
              ))}
            </PrepSection>
          )}
        </div>

        {/* Правая колонка */}
        <div>

          {/* Задачи */}
          <PrepSection title="Открытые задачи" icon="✅" accent={overdueTasks.length > 0 ? "var(--critical)" : "var(--ok)"}>
            {tasks.length === 0
              ? <div style={{ fontSize: 12, color: "var(--ink-4)" }}>Нет открытых задач</div>
              : <>
                {overdueTasks.length > 0 && (
                  <div style={{ marginBottom: 8 }}>
                    <div style={{ fontSize: 10, color: "var(--critical)", fontWeight: 700, marginBottom: 4 }}>ПРОСРОЧЕНО</div>
                    {overdueTasks.map((t, i) => (
                      <div key={i} style={{ fontSize: 12, color: "var(--ink-7)", padding: "4px 0", borderBottom: "1px solid var(--line)", display: "flex", gap: 8 }}>
                        <span style={{ color: "var(--critical)" }}>⏰</span>
                        <span style={{ flex: 1 }}>{t.title}</span>
                        <span style={{ fontSize: 11, color: "var(--ink-5)" }}>{t.due_date}</span>
                      </div>
                    ))}
                  </div>
                )}
                {openTasks.map((t, i) => (
                  <div key={i} style={{ fontSize: 12, color: "var(--ink-7)", padding: "5px 0", borderBottom: i < openTasks.length - 1 ? "1px solid var(--line)" : "none", display: "flex", gap: 8 }}>
                    <span style={{ color: t.priority === "high" ? "var(--warn)" : "var(--ink-4)" }}>
                      {t.status === "in_progress" ? "▶" : "○"}
                    </span>
                    <span style={{ flex: 1 }}>{t.title}</span>
                    {t.due_date && <span style={{ fontSize: 11, color: "var(--ink-5)" }}>{t.due_date}</span>}
                  </div>
                ))}
              </>
            }
          </PrepSection>

          {/* Контекст */}
          {context && (
            <PrepSection title="Контекст клиента" icon="🧠" accent="var(--signal)">
              {context.key_facts?.length > 0 && (
                <div style={{ marginBottom: 8 }}>
                  <div style={{ fontSize: 10, color: "var(--signal)", fontWeight: 700, marginBottom: 4 }}>КЛЮЧЕВЫЕ ФАКТЫ</div>
                  {context.key_facts.map((f, i) => <div key={i} style={{ fontSize: 12, color: "var(--ink-7)", marginBottom: 3 }}>• {f}</div>)}
                </div>
              )}
              {context.pain_points?.length > 0 && (
                <div style={{ marginBottom: 8 }}>
                  <div style={{ fontSize: 10, color: "var(--critical)", fontWeight: 700, marginBottom: 4 }}>БОЛИ</div>
                  {context.pain_points.map((p, i) => <div key={i} style={{ fontSize: 12, color: "var(--ink-7)", marginBottom: 3 }}>• {p}</div>)}
                </div>
              )}
              {context.next_steps?.length > 0 && (
                <div>
                  <div style={{ fontSize: 10, color: "var(--ok)", fontWeight: 700, marginBottom: 4 }}>СЛЕДУЮЩИЕ ШАГИ</div>
                  {context.next_steps.map((s, i) => <div key={i} style={{ fontSize: 12, color: "var(--ink-7)", marginBottom: 3 }}>→ {s}</div>)}
                </div>
              )}
            </PrepSection>
          )}

          {/* Заметка перед встречей */}
          <PrepSection title="Заметка перед встречей" icon="📝" accent="var(--ink-4)">
            <textarea value={note} onChange={e => setNote(e.target.value)} rows={4}
              placeholder="Что важно обсудить, вопросы, цели встречи…"
              style={{ width: "100%", background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 4, padding: "8px 10px", color: "var(--ink-9)", fontSize: 13, resize: "vertical", boxSizing: "border-box" }} />
            <div style={{ display: "flex", gap: 8, marginTop: 8, justifyContent: "flex-end" }}>
              {noteSaved && <span style={{ fontSize: 12, color: "var(--ok)", lineHeight: "34px" }}>✓ Сохранено</span>}
              <button onClick={saveNote}
                style={{ padding: "7px 14px", borderRadius: 4, cursor: "pointer", background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, fontSize: 13 }}>
                Сохранить заметку
              </button>
            </div>
          </PrepSection>

          {/* AI Брифинг для передачи */}
          <PrepSection title="Бриф для передачи клиента" icon="🤝" accent="var(--ink-4)">
            {briefText === null ? (
              <button onClick={loadTransferBrief} disabled={briefLoading}
                style={{ padding: "8px 14px", borderRadius: 4, cursor: "pointer", background: briefLoading ? "var(--ink-4)" : "transparent", border: "1px solid var(--line)", color: "var(--ink-6)", fontSize: 13 }}>
                {briefLoading ? "Генерирую…" : "✨ Сгенерировать AI-бриф"}
              </button>
            ) : (
              <div style={{ fontSize: 13, color: "var(--ink-7)", whiteSpace: "pre-wrap", lineHeight: 1.6 }}>
                {briefText}
                <div style={{ marginTop: 10 }}>
                  <button onClick={() => { navigator.clipboard.writeText(briefText); }}
                    style={{ fontSize: 11, padding: "4px 8px", borderRadius: 3, cursor: "pointer", background: "transparent", border: "1px solid var(--line)", color: "var(--ink-5)" }}>
                    Скопировать
                  </button>
                </div>
              </div>
            )}
          </PrepSection>
        </div>
      </div>
    </div>
  );
}

window.PagePrep = PagePrep;
