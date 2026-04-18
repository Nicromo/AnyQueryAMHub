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
            <Funnel rows={[
              { l: "Запланировано",  v: 148, pct: 100 },
              { l: "Состоялось",     v: 129, pct: 87 },
              { l: "С риском",       v: 38,  pct: 26 },
              { l: "Экшн-итем создан", v: 112, pct: 76 },
              { l: "Закрыто в 7д",   v: 84,  pct: 57 },
            ]}/>
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
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              {[
                { name: "Анна С.",  avg: "1ч 12м", tone: "ok" },
                { name: "Кирилл В.", avg: "2ч 04м", tone: "ok" },
                { name: "Лиза М.",  avg: "44м",    tone: "signal" },
                { name: "Павел Р.", avg: "4ч 30м", tone: "warn" },
              ].map((p, i) => (
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
                  <div style={{ fontSize: 22, fontWeight: 500, letterSpacing: "-0.02em", color: `var(--${p.tone})` }}>{p.avg}</div>
                  <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", marginTop: 2, textTransform: "uppercase", letterSpacing: "0.08em" }}>avg response</div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Heatmap() {
  const rows = ["СтройМаркет-21","ТехноЛайн","Gemini Shop","Aura Beauty","Kitchen Garden","Моя Полка","Dostavka Pro","Fiori Shop","Umbra Living","Vivo Tea","Nextfood","Lumen","Orbita","Raduga Mall"];
  const weeks = 7;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "140px repeat(7, 1fr)", gap: 2, alignItems: "center" }}>
      <span></span>
      {Array.from({length: weeks}).map((_, i) => (
        <span key={i} className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textAlign: "center", textTransform: "uppercase", letterSpacing: "0.08em" }}>
          W{10 + i}
        </span>
      ))}
      {rows.map((r, ri) => (
        <React.Fragment key={r}>
          <span className="mono" style={{ fontSize: 11, color: "var(--ink-7)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r}</span>
          {Array.from({length: weeks}).map((_, ci) => {
            const v = ((ri * 7 + ci * 31) % 100) / 100;
            const risk = (ri * 11 + ci * 7) % 23 < 3;
            const color = risk ? "var(--critical)" : "var(--signal)";
            return (
              <div key={ci} style={{
                height: 22, borderRadius: 2,
                background: `color-mix(in oklch, ${color} ${Math.round(v*90 + 6)}%, var(--ink-3))`,
              }} title={`${r} · W${10+ci}`}/>
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
  const days = 42;
  return (
    <div>
      <TopBar
        breadcrumbs={["am hub", "qbr календарь"]}
        title="QBR · квартальный ритм"
        subtitle="Q2'26 · 42 встречи запланировано · 7 в этой неделе"
        actions={<>
          <Btn kind="ghost" size="m" icon={<I.arrow_r size={14} style={{ transform: "rotate(180deg)" }}/>}>Март</Btn>
          <Btn kind="dim" size="m">Апрель</Btn>
          <Btn kind="ghost" size="m" iconRight={<I.arrow_r size={14}/>}>Май</Btn>
        </>}
      />
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 320px", gap: 18 }}>
        <Card title="Апрель · сетка встреч">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 3 }}>
            {["ПН","ВТ","СР","ЧТ","ПТ","СБ","ВС"].map(d => (
              <div key={d} className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textAlign: "center", textTransform: "uppercase", letterSpacing: "0.08em", padding: "4px 0" }}>{d}</div>
            ))}
            {Array.from({length: days}).map((_, i) => {
              const n = i - 1;
              const isThis = n === 17;
              const meetings = [5, 9, 12, 14, 17, 19, 21, 23, 26, 28].filter(x => x === n).length;
              const extraMeets = [2, 3, 5, 9, 12, 14, 17, 19, 21, 23, 26, 28].includes(n) ? (n === 17 ? 3 : n === 19 ? 2 : 1) : 0;
              return (
                <div key={i} style={{
                  height: 74, padding: 6,
                  background: isThis ? "color-mix(in oklch, var(--signal) 10%, var(--ink-2))" : "var(--ink-2)",
                  border: isThis ? "1px solid var(--signal)" : "1px solid var(--line)",
                  borderRadius: 3,
                  opacity: n < 0 || n > 29 ? 0.35 : 1,
                  display: "flex", flexDirection: "column", gap: 3,
                }}>
                  <div className="mono" style={{ fontSize: 10.5, color: isThis ? "var(--signal)" : "var(--ink-6)", fontWeight: 500 }}>
                    {n >= 0 && n <= 29 ? n + 1 : ""}
                  </div>
                  {Array.from({length: extraMeets}).map((_, j) => (
                    <div key={j} style={{
                      fontSize: 9.5, fontFamily: "var(--f-mono)",
                      padding: "1px 4px",
                      background: j === 0 && isThis ? "var(--critical)" : "var(--ink-3)",
                      color: j === 0 && isThis ? "var(--ink-0)" : "var(--ink-8)",
                      borderRadius: 2,
                      overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis",
                    }}>
                      {j === 0 && isThis ? "14:00 СтройМ." : j === 0 ? "10:30 ТехноЛ." : "15:00 др."}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        </Card>

        <Card title="Неделя 16 · предстоит">
          {[
            { d: "чт · 18 апр", client: "СтройМаркет-21", t: "14:00", seg: "A+", kind: "checkup" },
            { d: "чт · 18 апр", client: "ТехноЛайн",     t: "16:30", seg: "A",  kind: "QBR" },
            { d: "пт · 19 апр", client: "Kitchen Garden",t: "10:00", seg: "B",  kind: "onboarding" },
            { d: "пт · 19 апр", client: "Aura Beauty",   t: "15:00", seg: "A",  kind: "urgent" },
            { d: "сб · 20 апр", client: "Gemini Shop",   t: "11:00", seg: "B+", kind: "checkup" },
          ].map((m, i) => (
            <div key={i} style={{
              padding: "10px 0",
              borderBottom: i === 4 ? "none" : "1px solid var(--line-soft)",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{m.d}</span>
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-8)", fontWeight: 500 }}>{m.t}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: 13, color: "var(--ink-9)", fontWeight: 500 }}>{m.client}</span>
                <Seg value={m.seg}/>
              </div>
              <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", marginTop: 3, textTransform: "uppercase", letterSpacing: "0.08em" }}>
                {m.kind}
              </div>
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
}

window.PageAnalytics = PageAnalytics;
window.PageQBR = PageQBR;
