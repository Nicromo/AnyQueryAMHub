// page_analytics.jsx — Analytics + QBR Calendar

// Парсер GMV дублируется из page_hub — standalone безопасен
function _pga(str) {
  if (!str || typeof str !== "string") return 0;
  const n = parseFloat(str.replace(/[^\d.,]/g, "").replace(",", "."));
  if (isNaN(n)) return 0;
  if (str.includes("м")) return n * 1_000_000;
  if (str.includes("к")) return n * 1_000;
  return n;
}

function PageAnalytics() {
  const CL = (typeof window !== "undefined" && window.CLIENTS) || [];
  const totalGmv = CL.reduce((s, c) => s + _pga(c.gmv), 0);
  const gmvFmt = totalGmv >= 1_000_000 ? `₽ ${(totalGmv/1_000_000).toFixed(1)}м` : `₽ ${Math.round(totalGmv/1000)}к`;

  const okCount = CL.filter(c => c.status === "ok").length;
  const warnCount = CL.filter(c => c.status === "warn").length;
  const riskCount = CL.filter(c => c.status === "risk").length;
  const retentionPct = CL.length > 0 ? Math.round((okCount / CL.length) * 100) : 0;
  // "Health" — среднее: ok=85, warn=60, risk=35
  const avgHealth = CL.length > 0
    ? Math.round((okCount * 85 + warnCount * 60 + riskCount * 35) / CL.length)
    : 0;

  // Топ рисков
  const topRisks = CL
    .filter(c => c.status === "risk")
    .slice(0, 6)
    .map(c => ({
      id: c.id,
      c: c.name,
      why: c.delta ? `GMV ${c.delta}` : (c.days_since != null ? `не на связи ${c.days_since} дн.` : "требует внимания"),
      score: 100 - (c.days_since || 0),
    }));

  return (
    <div>
      <TopBar
        breadcrumbs={["am hub", "аналитика"]}
        title="Аналитика портфеля"
        subtitle={`${CL.length} клиентов в скоупе`}
        actions={<>
          <Btn kind="ghost" size="m" icon={<I.download size={14}/>}>PDF</Btn>
        </>}
      />
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
          <KPI label="GMV · портфель" value={gmvFmt} sub={`${CL.length} клиентов`} big/>
          <KPI label="Средний health" value={avgHealth} tone={avgHealth>=75?"ok":avgHealth>=55?"warn":"critical"} sub="из 100"/>
          <KPI label="Retention" value={`${retentionPct}%`} tone={retentionPct>=90?"signal":retentionPct>=70?"warn":"critical"} sub="доля клиентов в ok"/>
          <KPI label="В зоне риска" value={String(riskCount)} tone={riskCount>0?"critical":undefined} sub={`+ ${warnCount} warn`}/>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 18 }}>
          <Card title="Heatmap активности · 7 недель × 14 клиентов топ-портфеля">
            <Heatmap/>
          </Card>

          <Card title="Воронка чекапов · Q2">
            <Funnel rows={(function(){
              const MT = (typeof window !== "undefined" && window.MEETINGS) || [];
              const planned = MT.length;
              const held = MT.filter(m => m.status === "held" || m.status === "done").length;
              const risk  = MT.filter(m => (m.client && m.client.status === "risk")).length;
              const action = MT.filter(m => m.has_action_item).length;
              const closed = MT.filter(m => m.action_closed_7d).length;
              const pct = (n) => planned ? Math.round(n/planned*100) : 0;
              return [
                { l: "Запланировано",    v: planned, pct: planned ? 100 : 0 },
                { l: "Состоялось",       v: held,    pct: pct(held) },
                { l: "С риском",         v: risk,    pct: pct(risk) },
                { l: "Экшн-итем создан", v: action,  pct: pct(action) },
                { l: "Закрыто в 7д",     v: closed,  pct: pct(closed) },
              ];
            })()}/>
          </Card>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
          <Card title="Топ-риски" action={<Badge tone="critical" dot>{topRisks.length} активных</Badge>}>
            {topRisks.length === 0 && (
              <div style={{ padding: "20px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
                Нет клиентов в статусе risk.
              </div>
            )}
            {topRisks.map((r, i) => (
              <div key={i} style={{
                display: "grid", gridTemplateColumns: "1fr 90px 40px",
                gap: 12, padding: "10px 0",
                borderBottom: i === 3 ? "none" : "1px solid var(--line-soft)",
                alignItems: "center",
              }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-9)" }}>{r.c}</div>
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", marginTop: 2 }}>{r.why}</div>
                </div>
                <Progress value={r.score} tone="critical" h={3}/>
                <span className="mono" style={{ fontSize: 12, color: "var(--critical)", fontWeight: 500, textAlign: "right" }}>{r.score}</span>
              </div>
            ))}
          </Card>

          <Card title="Скорость реакции команды">
            {(function(){
              const team = (typeof window !== "undefined" && window.TEAM_RESPONSE) || [];
              if (!team.length) {
                return <div style={{ padding: "20px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
                  Данных о времени реакции пока нет.
                </div>;
              }
              return <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                {team.map((p, i) => (
                  <div key={i} style={{
                    padding: 12,
                    background: "var(--ink-1)",
                    border: "1px solid var(--line)",
                    borderRadius: 4,
                  }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
                      <Avatar name={p.name} size={22}/>
                      <span style={{ fontSize: 12.5, color: "var(--ink-8)", fontWeight: 500 }}>{p.name}</span>
                    </div>
                    <div style={{ fontSize: 22, fontWeight: 500, letterSpacing: "-0.02em", color: `var(--${p.tone || "ink-8"})` }}>{p.avg}</div>
                    <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", marginTop: 2, textTransform: "uppercase", letterSpacing: "0.08em" }}>avg response</div>
                  </div>
                ))}
              </div>;
            })()}
          </Card>
        </div>
      </div>
    </div>
  );
}

function Heatmap() {
  const heat = (typeof window !== "undefined" && window.HEATMAP) || null;
  const CL = (typeof window !== "undefined" && window.CLIENTS) || [];
  const rows = (heat && heat.rows) || CL.slice(0, 14).map(c => c.name).filter(Boolean);
  const weekLabels = (heat && heat.weeks) || [];
  const matrix = (heat && heat.matrix) || [];
  const weeks = weekLabels.length || 7;

  if (!rows.length) {
    return <div style={{ padding: "20px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
      Нет клиентов для отображения активности.
    </div>;
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: `140px repeat(${weeks}, 1fr)`, gap: 2, alignItems: "center" }}>
      <span></span>
      {Array.from({length: weeks}).map((_, i) => (
        <span key={i} className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textAlign: "center", textTransform: "uppercase", letterSpacing: "0.08em" }}>
          {weekLabels[i] || `W${i+1}`}
        </span>
      ))}
      {rows.map((r, ri) => (
        <React.Fragment key={r + ri}>
          <span className="mono" style={{ fontSize: 11, color: "var(--ink-7)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r}</span>
          {Array.from({length: weeks}).map((_, ci) => {
            const cell = matrix[ri] && matrix[ri][ci];
            const v = cell ? Math.max(0, Math.min(1, cell.value || 0)) : 0;
            const risk = cell && cell.risk;
            const color = risk ? "var(--critical)" : "var(--signal)";
            return (
              <div key={ci} style={{
                height: 22, borderRadius: 2,
                background: v > 0 ? `color-mix(in oklch, ${color} ${Math.round(v*90 + 6)}%, var(--ink-3))` : "var(--ink-3)",
              }} title={`${r} · ${weekLabels[ci] || `W${ci+1}`}`}/>
            );
          })}
        </React.Fragment>
      ))}
    </div>
  );
}

function Funnel({ rows }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {rows.map((r, i) => (
        <div key={i}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
            <span className="mono" style={{ fontSize: 11, color: "var(--ink-7)", textTransform: "uppercase", letterSpacing: "0.07em" }}>{r.l}</span>
            <span style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-8)" }}>{r.v} <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>· {r.pct}%</span></span>
          </div>
          <div style={{ height: 16, background: "var(--ink-3)", borderRadius: 2, overflow: "hidden" }}>
            <div style={{
              width: `${r.pct}%`, height: "100%",
              background: `color-mix(in oklch, var(--signal) ${80 - i*12}%, var(--ink-4))`,
            }}/>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── QBR calendar ─────────────────────────────────────────
function PageQBR() {
  const MT = (typeof window !== "undefined" && window.MEETINGS) || [];

  // Сортируем по дате и используем месяц ближайшей встречи (или текущий)
  const parseDate = (s) => { const d = new Date(s); return isNaN(d) ? null : d; };
  const mtDated = MT.map(m => ({ ...m, _d: parseDate(m.date || m.datetime || m.start) }))
                    .filter(m => m._d).sort((a,b) => a._d - b._d);

  const anchor = mtDated[0]?._d || new Date();
  const year = anchor.getFullYear();
  const month = anchor.getMonth();
  const monthName = anchor.toLocaleDateString("ru-RU", { month: "long" });
  const monthCap = monthName[0].toUpperCase() + monthName.slice(1);

  // Раскладка календаря
  const first = new Date(year, month, 1);
  const last = new Date(year, month + 1, 0);
  const daysInMonth = last.getDate();
  const startWeekday = (first.getDay() + 6) % 7;
  const today = new Date(); today.setHours(0,0,0,0);
  const cells = 42;

  const meetingsByDay = {};
  mtDated.forEach(m => {
    if (m._d.getFullYear() === year && m._d.getMonth() === month) {
      const day = m._d.getDate();
      (meetingsByDay[day] = meetingsByDay[day] || []).push(m);
    }
  });

  // Список встреч этой недели (от сегодня, неделя вперёд)
  const weekEnd = new Date(today); weekEnd.setDate(today.getDate() + 7);
  const thisWeek = mtDated.filter(m => m._d >= today && m._d < weekEnd).slice(0, 6);

  const subtitle = mtDated.length
    ? `${mtDated.length} встреч · ${thisWeek.length} в этой неделе`
    : "Встреч пока нет";

  return (
    <div>
      <TopBar
        breadcrumbs={["am hub", "qbr календарь"]}
        title="QBR · квартальный ритм"
        subtitle={subtitle}
      />
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 320px", gap: 18 }}>
        <Card title={`${monthCap} · сетка встреч`}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 3 }}>
            {["ПН","ВТ","СР","ЧТ","ПТ","СБ","ВС"].map(d => (
              <div key={d} className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textAlign: "center", textTransform: "uppercase", letterSpacing: "0.08em", padding: "4px 0" }}>{d}</div>
            ))}
            {Array.from({length: cells}).map((_, i) => {
              const dayNum = i - startWeekday + 1;
              const inMonth = dayNum >= 1 && dayNum <= daysInMonth;
              const cellDate = inMonth ? new Date(year, month, dayNum) : null;
              const isToday = cellDate && cellDate.getTime() === today.getTime();
              const dayMeets = (inMonth && meetingsByDay[dayNum]) || [];
              return (
                <div key={i} style={{
                  height: 74, padding: 6,
                  background: isToday ? "color-mix(in oklch, var(--signal) 10%, var(--ink-2))" : "var(--ink-2)",
                  border: isToday ? "1px solid var(--signal)" : "1px solid var(--line)",
                  borderRadius: 3,
                  opacity: inMonth ? 1 : 0.35,
                  display: "flex", flexDirection: "column", gap: 3,
                  overflow: "hidden",
                }}>
                  <div className="mono" style={{ fontSize: 10.5, color: isToday ? "var(--signal)" : "var(--ink-6)", fontWeight: 500 }}>
                    {inMonth ? dayNum : ""}
                  </div>
                  {dayMeets.slice(0, 3).map((m, j) => {
                    const t = m._d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
                    const name = (m.client_name || (m.client && m.client.name) || m.title || "").slice(0, 8);
                    const urgent = m.priority === "urgent" || m.urgent;
                    return (
                      <div key={j} style={{
                        fontSize: 9.5, fontFamily: "var(--f-mono)",
                        padding: "1px 4px",
                        background: urgent ? "var(--critical)" : "var(--ink-3)",
                        color: urgent ? "var(--ink-0)" : "var(--ink-8)",
                        borderRadius: 2,
                        overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis",
                      }}>
                        {t} {name}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </Card>

        <Card title="Ближайшие · 7 дней">
          {thisWeek.length === 0 && (
            <div style={{ padding: "20px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
              Встреч на неделю нет.
            </div>
          )}
          {thisWeek.map((m, i) => {
            const d = m._d.toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "short" });
            const t = m._d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
            const name = m.client_name || (m.client && m.client.name) || m.title || "—";
            const seg = (m.client && m.client.segment) || m.segment;
            const kind = m.type || m.kind || "meeting";
            return (
              <div key={i} style={{
                padding: "10px 0",
                borderBottom: i === thisWeek.length - 1 ? "none" : "1px solid var(--line-soft)",
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
                  <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{d}</span>
                  <span className="mono" style={{ fontSize: 11, color: "var(--ink-8)", fontWeight: 500 }}>{t}</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 13, color: "var(--ink-9)", fontWeight: 500 }}>{name}</span>
                  {seg && <Seg value={seg}/>}
                </div>
                <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", marginTop: 3, textTransform: "uppercase", letterSpacing: "0.08em" }}>
                  {kind}
                </div>
              </div>
            );
          })}
        </Card>
      </div>
    </div>
  );
}

window.PageAnalytics = PageAnalytics;
window.PageQBR = PageQBR;
