// page_today.jsx — Today & Tasks

// ── Фокус дня виджет ─────────────────────────────────────────────────────────

function FocusWidget() {
  const [items, setItems]   = React.useState(null);
  const [sending, setSending] = React.useState({});

  React.useEffect(() => {
    fetch("/api/today/focus", { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setItems(d.items || []); });
  }, []);

  const sendFollowup = async (item) => {
    setSending(s => ({ ...s, [item.id]: true }));
    const r = await fetch(`/api/clients/${item.id}/quick-followup`, { method: "POST", credentials: "include" });
    setSending(s => ({ ...s, [item.id]: false }));
    const data = r.ok ? await r.json() : null;
    if (data?.ok) window.appToast && window.appToast("Фолоуап отправлен", "ok");
    else window.appToast && window.appToast(data?.error || "Ошибка отправки", "error");
  };

  if (items === null) return (
    <div style={{ padding: "14px 0", color: "var(--ink-5)", fontSize: 12, textAlign: "center" }}>Загрузка фокуса…</div>
  );

  if (items.length === 0) return (
    <div style={{ padding: "14px 0", color: "var(--ink-5)", fontSize: 12.5, textAlign: "center" }}>
      Всё под контролем — нет клиентов требующих внимания сегодня 🎉
    </div>
  );

  const toneOf = (priority) => priority >= 80 ? "critical" : priority >= 60 ? "warn" : "signal";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {items.map((item) => {
        const tone = toneOf(item.priority);
        const borderColor = tone === "critical" ? "var(--critical)" : tone === "warn" ? "var(--warn)" : "var(--signal)";
        return (
          <div key={item.id} style={{
            display: "flex", alignItems: "flex-start", gap: 12,
            padding: "11px 14px",
            borderRadius: 6,
            background: "var(--ink-2)",
            border: `1px solid color-mix(in oklch, ${borderColor} 30%, transparent)`,
            borderLeft: `3px solid ${borderColor}`,
            marginBottom: 4,
          }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-9)", marginBottom: 4 }}>
                {item.name}
                {item.segment && <span style={{ fontSize: 10, color: "var(--ink-5)", marginLeft: 8 }}>{item.segment}</span>}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {item.reasons.map((r, i) => (
                  <span key={i} style={{ fontSize: 11, color: "var(--ink-6)" }}>{r}</span>
                ))}
              </div>
            </div>
            <div style={{ display: "flex", gap: 5, flexShrink: 0 }}>
              <button
                onClick={() => { window.location.href = `/design/prep?client_id=${item.id}`; }}
                style={{ fontSize: 11, padding: "4px 8px", borderRadius: 4, cursor: "pointer",
                  background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500 }}>
                Подготовиться
              </button>
              <button
                onClick={() => sendFollowup(item)}
                disabled={sending[item.id]}
                style={{ fontSize: 11, padding: "4px 8px", borderRadius: 4, cursor: "pointer",
                  background: "transparent", border: "1px solid var(--line)", color: "var(--ink-6)" }}>
                {sending[item.id] ? "…" : "Фолоуап"}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Главная страница ──────────────────────────────────────────────────────────

function PageToday() {
  const TK = (typeof window !== "undefined" && window.TASKS)    || [];
  const MT = (typeof window !== "undefined" && window.MEETINGS) || [];
  const CL = (typeof window !== "undefined" && window.CLIENTS)  || [];
  const U  = (typeof window !== "undefined" && window.__CURRENT_USER) || {};

  const todayMeetings = MT.filter(m => m.day === "сегодня");
  const overdueTasks  = TK.filter(t => (t.due || "").indexOf("просроч") !== -1);
  const todayTasks    = TK.filter(t => t.due === "сегодня");
  const firstRisk     = CL.find(c => c.status === "risk");

  // Таймлайн встреч (с кнопкой подготовки)
  const timeline = todayMeetings.map(m => ({
    t:        m.when || "—",
    item:     `${m.type === "qbr" ? "QBR" : m.type === "checkup" ? "Чекап" : "Встреча"} · ${m.client}`,
    client_id: m.client_id,
    place:    "KTalk",
    tone:     m.mood === "risk" ? "critical" : m.mood === "warn" ? "warn" : "signal",
  })).sort((a, b) => (a.t > b.t ? 1 : -1));

  const now = new Date();
  const weekdays = ["воскресенье","понедельник","вторник","среда","четверг","пятница","суббота"];
  const months = ["января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"];
  const dateLabel = `${weekdays[now.getDay()]}, ${now.getDate()} ${months[now.getMonth()]}`;

  const firstName = (U.name || "").split(" ")[0] || "коллега";
  const greet = now.getHours() < 12 ? "Доброе утро" : now.getHours() < 18 ? "Добрый день" : "Добрый вечер";

  const riskCount = CL.filter(c => c.status === "risk").length;
  const warnCount = CL.filter(c => c.status === "warn").length;

  return (
    <div>
      <TopBar
        breadcrumbs={["am hub", "ежедневное", "сегодня"]}
        title={`Сегодня · ${dateLabel}`}
        subtitle={`${todayMeetings.length} встреч · ${todayTasks.length} задач сегодня · ${overdueTasks.length} просрочено`}
        actions={<>
          <Btn kind="ghost" size="m" icon={<I.mic size={14}/>}
            onClick={() => { window.location.href = "/design/cabinet?tab=voice"; }}>
            Голосовая заметка
          </Btn>
          <Btn kind="primary" size="m" icon={<I.lightning size={14}/>}
            onClick={async () => {
              try {
                const r = await fetch("/api/ai/generate-daily-plan", {
                  method: "POST", credentials: "include",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({}),
                });
                if (r.ok) { window.appToast && window.appToast("План готов"); window.location.reload(); }
                else { window.appToast && window.appToast("Не удалось сгенерировать"); }
              } catch (e) { window.appToast && window.appToast("Ошибка сети"); }
            }}>
            Сгенерить план
          </Btn>
        </>}
      />
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 340px", gap: 18 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0 }}>

          {/* AI morning brief */}
          <div style={{
            padding: 20, borderRadius: 8,
            border: "1px solid color-mix(in oklch, var(--signal) 25%, transparent)",
            background: "linear-gradient(135deg, color-mix(in oklch, var(--signal) 6%, transparent), transparent 60%), var(--ink-2)",
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
              <I.spark size={14} stroke="var(--signal)"/>
              <span className="mono" style={{ fontSize: 10.5, color: "var(--signal)", textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 500 }}>
                утренний бриф · {String(now.getHours()).padStart(2,"0")}:{String(now.getMinutes()).padStart(2,"0")}
              </span>
            </div>
            <div style={{ fontSize: 16, color: "var(--ink-9)", lineHeight: 1.55, letterSpacing: "-0.005em" }}>
              {greet}, {firstName}.{" "}
              {todayMeetings.length === 0 && overdueTasks.length === 0 && !firstRisk && (
                <>Сегодня ничего срочного — отличный день для проактивной работы с портфелем.</>
              )}
              {todayMeetings.length > 0 && (
                <>
                  Сегодня <b style={{ color: "var(--signal)" }}>
                    {todayMeetings.length} {todayMeetings.length === 1 ? "встреча" : todayMeetings.length < 5 ? "встречи" : "встреч"}
                  </b>
                  {todayMeetings[0] && (<>, ближайшая — {todayMeetings[0].type || "встреча"} с {todayMeetings[0].client} в {todayMeetings[0].when}</>)}.
                </>
              )}
              {overdueTasks.length > 0 && (
                <>
                  <br/><br/>
                  <b style={{ color: "var(--critical)" }}>{overdueTasks.length} {overdueTasks.length === 1 ? "просроченная задача" : "просроченных задач"}</b>
                  {overdueTasks[0] && (<> — начни с «{overdueTasks[0].title}»</>)}.
                </>
              )}
              {(riskCount > 0 || warnCount > 0) && (
                <>
                  <br/><br/>
                  В портфеле: <b style={{ color: "var(--critical)" }}>{riskCount} в риске</b>
                  {warnCount > 0 && <>, <b style={{ color: "var(--warn)" }}>{warnCount} в зоне внимания</b></>}.
                </>
              )}
            </div>
          </div>

          {/* Фокус дня */}
          <Card
            title="Фокус дня"
            action={
              <span className="mono" style={{ fontSize: 11, color: "var(--ink-5)" }}>
                приоритет по рискам
              </span>
            }
          >
            <FocusWidget />
          </Card>

          {/* Таймлайн дня с кнопкой подготовки */}
          <Card title="Таймлайн дня" action={
            <span className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>
              {timeline.length} {timeline.length === 1 ? "событие" : "событий"}
            </span>
          }>
            <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
              {timeline.length === 0 && (
                <div style={{ padding: "20px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
                  Сегодня встреч нет — свободный день для фокусной работы.
                </div>
              )}
              {timeline.map((r, i) => (
                <div key={i} style={{
                  display: "grid", gridTemplateColumns: "60px 14px 1fr auto",
                  gap: 12, padding: "10px 0",
                  borderBottom: i === timeline.length - 1 ? "none" : "1px solid var(--line-soft)",
                  alignItems: "center",
                }}>
                  <div className="mono" style={{ fontSize: 12, color: "var(--ink-7)" }}>{r.t}</div>
                  <span style={{
                    width: 10, height: 10, borderRadius: 999,
                    background: r.tone === "critical" ? "var(--critical)" : r.tone === "signal" ? "var(--signal)" : r.tone === "warn" ? "var(--warn)" : "var(--ink-4)",
                    justifySelf: "center",
                    boxShadow: r.tone !== "neutral" ? `0 0 8px ${r.tone === "critical" ? "var(--critical)" : r.tone === "signal" ? "var(--signal)" : "var(--warn)"}` : "none",
                  }}/>
                  <div>
                    <div style={{ fontSize: 13, color: "var(--ink-8)", fontWeight: 500 }}>{r.item}</div>
                    {r.place && <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>{r.place}</div>}
                  </div>
                  {r.client_id && (
                    <button
                      onClick={() => { window.location.href = `/design/prep?client_id=${r.client_id}`; }}
                      style={{ fontSize: 11, padding: "4px 10px", borderRadius: 4, cursor: "pointer", whiteSpace: "nowrap",
                        background: "var(--signal)", border: "none", color: "var(--ink-0)", fontWeight: 500, flexShrink: 0 }}>
                      Подготовиться
                    </button>
                  )}
                </div>
              ))}
            </div>
          </Card>

          {/* Очередь задач */}
          <Card title="Очередь задач" action={
            <div style={{ display: "flex", gap: 6 }}>
              <Btn size="s" kind="dim">{`все · ${TK.length}`}</Btn>
              <Btn size="s" kind="ghost">{`сегодня · ${todayTasks.length}`}</Btn>
              <Btn size="s" kind="ghost" style={{ color: overdueTasks.length ? "var(--critical)" : undefined }}>
                {`просроч. · ${overdueTasks.length}`}
              </Btn>
            </div>
          }>
            {TK.length === 0 && (
              <div style={{ padding: "20px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
                Открытых задач нет.
              </div>
            )}
            <div>
              {TK.map((t, i) => {
                const isDone   = t.status === "done" || !!t.done;
                const isOverdue = (t.due || "").includes("просроч");
                return (
                  <div key={i} style={{
                    display: "grid", gridTemplateColumns: "20px 1fr 90px 120px 24px",
                    gap: 12, padding: "12px 4px",
                    borderBottom: i === TK.length-1 ? "none" : "1px solid var(--line-soft)",
                    alignItems: "center",
                    background: isOverdue ? "color-mix(in oklch, var(--critical) 4%, transparent)" : "transparent",
                    borderRadius: isOverdue ? 4 : 0,
                  }}>
                    <TaskCheck checked={isDone}
                      onChange={async (checked) => {
                        if (!t.id) return;
                        const newStatus = checked ? "done" : "plan";
                        try {
                          const resp = await fetch(`/api/tasks/${t.id}/status`, {
                            method: "PATCH", credentials: "include",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ status: newStatus }),
                          });
                          if (!resp.ok) throw new Error("HTTP " + resp.status);
                          appToast(newStatus === "done" ? "Задача закрыта" : "Задача открыта", "ok");
                          location.reload();
                        } catch (err) { appToast("Ошибка: " + err.message, "error"); }
                      }}/>
                    <div>
                      <div style={{ fontSize: 13, color: isDone ? "var(--ink-5)" : "var(--ink-8)", textDecoration: isDone ? "line-through" : "none" }}>{t.title}</div>
                      <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", marginTop: 3 }}>{t.client}</div>
                    </div>
                    <Badge tone={t.priority === "critical" ? "critical" : t.priority === "high" ? "warn" : t.priority === "med" ? "info" : "neutral"} dot>
                      {t.priority}
                    </Badge>
                    <span className="mono" style={{ fontSize: 11, color: isOverdue ? "var(--critical)" : "var(--ink-6)", fontWeight: isOverdue ? 600 : 400 }}>
                      {t.due}
                    </span>
                    <I.dot3 size={14} stroke="var(--ink-6)"/>
                  </div>
                );
              })}
            </div>
          </Card>
        </div>

        {/* Правая колонка */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0 }}>

          {/* KPI дня */}
          {(function(){
            const S = (typeof window !== "undefined" && window.DAY_KPI) || null;
            if (!S) return null;
            const rows = [
              { k: "Встречи",        v: `${S.meetings_done||0}/${S.meetings_total||0}`, pct: S.meetings_pct||0, tone: "signal" },
              { k: "Задачи закрыты", v: `${S.tasks_done||0}/${S.tasks_total||0}`, pct: S.tasks_pct||0, tone: S.tasks_pct>=60?"ok":"warn" },
            ];
            return <Card title="KPI дня" dense>
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {rows.map((r, i) => (
                  <div key={i}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                      <span className="mono" style={{ fontSize: 11, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{r.k}</span>
                      <span style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-8)" }}>{r.v}</span>
                    </div>
                    <Progress value={r.pct} tone={r.tone} h={3}/>
                  </div>
                ))}
              </div>
            </Card>;
          })()}

          {/* Быстрые действия */}
          <Card title="Быстрые действия" dense>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {[
                { label: "📋 Все клиенты",       href: "/design/clients"   },
                { label: "✅ Задачи",             href: "/design/tasks"     },
                { label: "📅 Встречи",            href: "/design/meetings"  },
                { label: "💡 Гипотезы",           href: "/design/hypotheses" },
                { label: "📢 TG-рассылка",        href: "/design/broadcast" },
                { label: "🧠 Контекст клиентов",  href: "/design/context"   },
                { label: "🗺 Роадмап",            href: "/design/roadmap"   },
              ].map((a, i) => (
                <a key={i} href={a.href} style={{
                  display: "block", padding: "8px 10px", borderRadius: 5,
                  background: "var(--ink-2)", border: "1px solid var(--line-soft)",
                  color: "var(--ink-7)", fontSize: 13, textDecoration: "none",
                  transition: "background 0.15s",
                }}
                onMouseEnter={e => e.currentTarget.style.background = "var(--ink-3)"}
                onMouseLeave={e => e.currentTarget.style.background = "var(--ink-2)"}
                >
                  {a.label}
                </a>
              ))}
            </div>
          </Card>

          {/* Напоминания */}
          {(function(){
            const R = (typeof window !== "undefined" && window.REMINDERS) || [];
            if (!R.length) return (
              <Card title="Напоминания" dense>
                <div style={{ padding: "12px 0", color: "var(--ink-6)", fontSize: 12.5, textAlign: "center" }}>
                  Нет запланированных напоминаний.
                </div>
              </Card>
            );
            return <Card title="Напоминания">
              {R.map((r, i) => (
                <div key={i} style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "10px 0",
                  borderBottom: i === R.length - 1 ? "none" : "1px solid var(--line-soft)",
                  opacity: r.done ? 0.45 : 1,
                }}>
                  <I.bell size={13} stroke={r.done ? "var(--ink-5)" : "var(--signal)"}/>
                  <div style={{ flex: 1, fontSize: 12.5, color: "var(--ink-8)", textDecoration: r.done ? "line-through" : "none" }}>{r.msg || r.text}</div>
                  <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>{r.t || r.time}</span>
                </div>
              ))}
            </Card>;
          })()}
        </div>
      </div>
    </div>
  );
}

window.PageToday = PageToday;
