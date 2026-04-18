// page_today.jsx — Today & Tasks

function PageToday() {
  return (
    <div>
      <TopBar
        breadcrumbs={["am hub", "ежедневное", "сегодня"]}
        title="Сегодня · четверг, 18 апреля"
        subtitle="3 встречи · 5 задач · 2 сигнала требуют действия"
        actions={<>
          <Btn kind="ghost" size="m" icon={<I.mic size={14}/>}>Голосовая заметка</Btn>
          <Btn kind="primary" size="m" icon={<I.lightning size={14}/>}>Сгенерить план</Btn>
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
              <span className="mono" style={{ fontSize: 10.5, color: "var(--signal)", textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 500 }}>утренний бриф · 09:12</span>
            </div>
            <div style={{ fontSize: 16, color: "var(--ink-9)", lineHeight: 1.55, letterSpacing: "-0.005em" }}>
              Доброе утро, Анна. Сегодня <b style={{ color: "var(--signal)" }}>ключевая встреча</b> — чекап со СтройМаркет-21 в 14:00. Они выросли на 12%, но воронка оплаты просела.
              <br/><br/>
              До обеда закрой задачу по Aura Beauty (она просрочена 2 дня и блокирует QBR). Вечером — подготовка презентации для ТехноЛайн.
            </div>
          </div>

          {/* timeline */}
          <Card title="Таймлайн дня">
            <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
              {[
                { t: "09:00", item: "Утренняя планёрка команды", place: "KTalk", tone: "neutral" },
                { t: "10:30", item: "Подготовка: лимиты вывода для СтройМаркет-21", place: "focus time · 90 мин", tone: "signal" },
                { t: "12:00", item: "Обед", place: "", tone: "neutral" },
                { t: "14:00", item: "Чекап · СтройМаркет-21", place: "KTalk · Ольга Ларина", tone: "critical" },
                { t: "15:00", item: "QBR-подготовка · Aura Beauty", place: "focus time · 60 мин", tone: "warn" },
                { t: "16:30", item: "QBR · ТехноЛайн", place: "онлайн · 4 участника", tone: "signal" },
                { t: "18:00", item: "Дайджест недели · команда", place: "Telegram", tone: "neutral" },
              ].map((r, i) => (
                <div key={i} style={{
                  display: "grid", gridTemplateColumns: "60px 14px 1fr",
                  gap: 12, padding: "10px 0",
                  borderBottom: "1px solid var(--line-soft)",
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
                </div>
              ))}
            </div>
          </Card>

          {/* tasks inbox */}
          <Card title="Очередь задач" action={
            <div style={{ display: "flex", gap: 6 }}>
              <Btn size="s" kind="dim">все · 37</Btn>
              <Btn size="s" kind="ghost">мои · 12</Btn>
              <Btn size="s" kind="ghost">просроч. · 3</Btn>
            </div>
          }>
            <div>
              {TASKS.map((t, i) => (
                <div key={i} style={{
                  display: "grid", gridTemplateColumns: "20px 1fr 90px 120px 24px",
                  gap: 12, padding: "12px 4px",
                  borderBottom: i === TASKS.length-1 ? "none" : "1px solid var(--line-soft)",
                  alignItems: "center",
                }}>
                  <input type="checkbox" style={{ accentColor: "var(--signal)" }}/>
                  <div>
                    <div style={{ fontSize: 13, color: "var(--ink-8)" }}>{t.title}</div>
                    <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", marginTop: 3 }}>{t.client}</div>
                  </div>
                  <Badge tone={t.priority === "critical" ? "critical" : t.priority === "high" ? "warn" : t.priority === "med" ? "info" : "neutral"} dot>
                    {t.priority}
                  </Badge>
                  <span className="mono" style={{ fontSize: 11, color: t.due.includes("просроч") ? "var(--critical)" : "var(--ink-6)" }}>{t.due}</span>
                  <I.dot3 size={14} stroke="var(--ink-6)"/>
                </div>
              ))}
            </div>
          </Card>
        </div>

        {/* right column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0 }}>

          <Card title="KPI дня" dense>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {[
                { k: "Встречи", v: "2/3", pct: 67, tone: "signal" },
                { k: "Задачи закрыты", v: "3/8", pct: 38, tone: "warn" },
                { k: "Ответов в час", v: "6.4", pct: 80, tone: "ok" },
                { k: "Фокус-время", v: "2.5ч", pct: 50, tone: "info" },
              ].map((r, i) => (
                <div key={i}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                    <span className="mono" style={{ fontSize: 11, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{r.k}</span>
                    <span style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-8)" }}>{r.v}</span>
                  </div>
                  <Progress value={r.pct} tone={r.tone} h={3}/>
                </div>
              ))}
            </div>
          </Card>

          <Card title="Напоминания">
            {[
              { t: "09:45", msg: "Подготовить материалы к 14:00", done: true },
              { t: "13:30", msg: "Перезвонить Денису по договору" },
              { t: "17:00", msg: "Заполнить чекап-результат" },
              { t: "18:30", msg: "Сдать отчёт за неделю" },
            ].map((r, i) => (
              <div key={i} style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "10px 0",
                borderBottom: i === 3 ? "none" : "1px solid var(--line-soft)",
                opacity: r.done ? 0.45 : 1,
              }}>
                <I.bell size={13} stroke={r.done ? "var(--ink-5)" : "var(--signal)"}/>
                <div style={{ flex: 1, fontSize: 12.5, color: "var(--ink-8)", textDecoration: r.done ? "line-through" : "none" }}>{r.msg}</div>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>{r.t}</span>
              </div>
            ))}
          </Card>

          <Card title="Стрик" dense>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 10 }}>
              <span style={{ fontSize: 38, fontWeight: 500, color: "var(--signal)", letterSpacing: "-0.03em", lineHeight: 1 }}>14</span>
              <span className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>дней подряд</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(14, 1fr)", gap: 3 }}>
              {Array.from({length: 14}).map((_, i) => (
                <div key={i} style={{
                  height: 18,
                  background: `color-mix(in oklch, var(--signal) ${Math.min(80, 20 + i*5)}%, var(--ink-3))`,
                  borderRadius: 2,
                }}/>
              ))}
            </div>
            <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", marginTop: 8 }}>
              все ежедневные цели выполнены
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

window.PageToday = PageToday;
