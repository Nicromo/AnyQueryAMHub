// page_more.jsx — remaining tabs (top50, tasks, meetings, portfolio, ai, kanban, kpi, cabinet, templates, auto, roadmap, internal, extension-install, help)

// ── Top-50 ────────────────────────────────────────────────
// Парсер GMV-строки дублируется между страницами — локальный,
// чтобы не городить глобалы. _parseGmv определён в page_hub.jsx.
function _pg(str) {
  if (!str || typeof str !== "string") return 0;
  const n = parseFloat(str.replace(/[^\d.,]/g, "").replace(",", "."));
  if (isNaN(n)) return 0;
  if (str.includes("м")) return n * 1_000_000;
  if (str.includes("к")) return n * 1_000;
  return n;
}

function PageTop50() {
  const CL = (typeof window !== "undefined" && window.CLIENTS) || [];
  // Сортировка по убыванию GMV, топ-50
  const rows = CL
    .slice()
    .map((c, idx) => ({
      rk: idx + 1,  // временный, перезапишем после сортировки
      id: c.id,
      name: c.name,
      seg: c.seg,
      gmv: c.gmv,
      gmvRub: _pg(c.gmv),
      growth: c.delta || "—",
      // health — грубая оценка: status ok=85, warn=60, risk=35
      health: c.status === "risk" ? 35 : c.status === "warn" ? 60 : 85,
      pm: c.pm,
    }))
    .sort((a, b) => b.gmvRub - a.gmvRub)
    .slice(0, 50)
    .map((r, i) => ({ ...r, rk: i + 1 }));

  // Агрегаты для KPI
  const totalRub = rows.reduce((s, r) => s + r.gmvRub, 0);
  const avgHealth = rows.length ? Math.round(rows.reduce((s, r) => s + r.health, 0) / rows.length) : 0;
  const atRisk = rows.filter(r => r.health < 55).length;
  const growing = rows.filter(r => (r.growth || "").startsWith("+")).length;
  return (
    <div>
      <TopBar breadcrumbs={["am hub","top-50"]} title="Top-50 · приоритетный портфель"
        subtitle="Клиенты, формирующие 78% GMV команды"
        actions={<><Btn kind="ghost" size="m" icon={<I.filter size={14}/>}>Фильтр</Btn><Btn kind="primary" size="m" icon={<I.download size={14}/>}>PDF-отчёт</Btn></>}/>
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12 }}>
          <KPI label={`Top-${rows.length} · GMV`} value={totalRub >= 1_000_000 ? `₽ ${(totalRub/1_000_000).toFixed(1)}м` : totalRub >= 1000 ? `₽ ${Math.round(totalRub/1000)}к` : `₽ ${Math.round(totalRub)}`} sub={`${rows.length} клиентов`} big/>
          <KPI label="Средний health" value={avgHealth} tone={avgHealth>=75?"ok":avgHealth>=55?"warn":"critical"}/>
          <KPI label="Под риском" value={atRisk} tone={atRisk>0?"critical":undefined} sub={`из ${rows.length}`}/>
          <KPI label="Растут" value={growing} tone="ok" sub="клиентов"/>
        </div>

        <Card title="Рейтинг · апрель 2026">
          <div style={{ background: "var(--ink-2)", borderRadius: 4 }}>
            <div style={{ display: "grid", gridTemplateColumns: "50px 1.6fr 70px 110px 90px 1fr 110px", gap: 14, padding: "10px 10px", background: "var(--ink-1)", borderRadius: 4, fontFamily: "var(--f-mono)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--ink-5)" }}>
              <span>#</span><span>клиент</span><span>seg</span><span>gmv 30д</span><span>Δ</span><span>health</span><span>am</span>
            </div>
            {rows.length === 0 && (
              <div style={{ padding: "28px", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
                Нет клиентов в скоупе. После первой синхронизации с Merchrules — они появятся здесь.
              </div>
            )}
            {rows.map((r, i) => (
              <div key={r.rk}
                onClick={() => { if (r.id) window.location.href = "/design/client/" + r.id; }}
                style={{ display: "grid", gridTemplateColumns: "50px 1.6fr 70px 110px 90px 1fr 110px", gap: 14, padding: "12px 10px", alignItems: "center", borderBottom: i===rows.length-1?"none":"1px solid var(--line-soft)", cursor: "pointer" }}>
                <span className="mono" style={{ fontSize: 13, fontWeight: 500, color: r.rk <= 3 ? "var(--signal)" : "var(--ink-6)" }}>{String(r.rk).padStart(2,"0")}</span>
                <span style={{ fontSize: 13, color: "var(--ink-9)", fontWeight: 500 }}>{r.name}</span>
                <Seg value={r.seg}/>
                <span className="mono" style={{ fontSize: 12, color: "var(--ink-8)" }}>{r.gmv}</span>
                <span className="mono" style={{ fontSize: 12, color: (r.growth||"").startsWith("−") ? "var(--critical)" : "var(--ok)", fontWeight: 500 }}>{r.growth}</span>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={{ flex: 1 }}><Progress value={r.health} tone={r.health>=75?"ok":r.health>=55?"warn":"critical"} h={3}/></div>
                  <span className="mono" style={{ fontSize: 11, color: "var(--ink-7)", width: 22, textAlign: "right" }}>{r.health}</span>
                </div>
                <span style={{ fontSize: 12, color: "var(--ink-7)" }}>{r.pm}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

// ── Tasks ─────────────────────────────────────────────────
function PageTasks() {
  const TK = (typeof window !== "undefined" && window.TASKS) || [];

  // Группировка реальных задач по колонкам.
  // На сервере (design_mappers) статус не передаётся в task-dict — есть только due и priority.
  // Поэтому колонки делим по смыслу due: просрочено → "В работе" (требует внимания),
  // сегодня → "Сегодня", через N дн. → "Бэклог". Готовые (status=done) не приходят с сервера.
  const today = [], soon = [], backlog = [], overdue = [];
  TK.forEach(t => {
    const due = (t.due || "").toLowerCase();
    if (due.indexOf("просроч") !== -1) overdue.push(t);
    else if (due === "сегодня") today.push(t);
    else if (due === "завтра" || due.indexOf("через") !== -1) backlog.push(t);
    else backlog.push(t);
  });

  const cols = [
    { title: "Просрочено", tone: "critical", count: overdue.length, items: overdue.slice(0, 8).map(t => ({ t: t.title, cl: t.client, pr: t.priority })) },
    { title: "Сегодня",    tone: "signal",   count: today.length,   items: today.slice(0, 8).map(t => ({ t: t.title, cl: t.client, pr: t.priority })) },
    { title: "В работе",   tone: "warn",     count: 0,              items: [] },  // нет источника (status=in_progress приходят в today/backlog)
    { title: "Бэклог",     tone: "neutral",  count: backlog.length, items: backlog.slice(0, 8).map(t => ({ t: t.title, cl: t.client, pr: t.priority })) },
  ];
  const totalActive = TK.length;
  return (
    <div>
      <TopBar breadcrumbs={["am hub","задачи"]} title="Задачи · канбан"
        subtitle={`${totalActive} активных · ${overdue.length} просрочено · ${today.length} на сегодня`}
        actions={<><Btn kind="ghost" size="m">Мои</Btn><Btn kind="dim" size="m">Вся команда</Btn><Btn kind="primary" size="m" icon={<I.plus size={14}/>} onClick={() => {
          // Клик на "+" открывает глобальную модалку из shell (FAB всегда в DOM)
          document.querySelector('button[title="Новая задача"]')?.click();
        }}>Задача</Btn></>}/>
      <div style={{ padding: "22px 28px 40px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14 }}>
          {cols.map((c, i) => (
            <div key={i} style={{ background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6, display: "flex", flexDirection: "column", minHeight: 480 }}>
              <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--line-soft)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 6, height: 6, borderRadius: 999, background: c.tone==="signal"?"var(--signal)":c.tone==="warn"?"var(--warn)":c.tone==="ok"?"var(--ok)":"var(--ink-5)" }}/>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>{c.title}</span>
                </div>
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-5)" }}>{c.count}</span>
              </div>
              <div style={{ padding: 10, display: "flex", flexDirection: "column", gap: 8, flex: 1 }}>
                {c.items.map((it, j) => (
                  <div key={j} style={{ padding: 12, background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 4, cursor: "grab" }}>
                    <div style={{ fontSize: 12.5, color: "var(--ink-8)", lineHeight: 1.4, marginBottom: 8 }}>{it.t}</div>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <span className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{it.cl}</span>
                      <Badge tone={it.pr==="critical"?"critical":it.pr==="high"?"warn":it.pr==="med"?"info":"neutral"} dot>{it.pr}</Badge>
                    </div>
                  </div>
                ))}
                <button style={{ marginTop: 4, padding: "8px 10px", background: "transparent", border: "1px dashed var(--line)", borderRadius: 4, color: "var(--ink-5)", cursor: "pointer", fontFamily: "var(--f-mono)", fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase" }}>+ добавить</button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Meetings ──────────────────────────────────────────────
function PageMeetings() {
  const MT = (typeof window !== "undefined" && window.MEETINGS) || [];

  // Маппинг из window.MEETINGS (server-shape: {when, day, client, type, seg, mood})
  // в UI-shape: {d, cl, kind, seg, who, ch, mood}.
  const meets = MT.map(m => ({
    d: `${m.day || "—"} · ${m.when || ""}`.trim(),
    cl: m.client || "—",
    kind: m.type || "sync",
    seg: m.seg || "—",
    who: "—",   // attendees не пробрасываются с сервера (JSONB поле)
    ch: "",     // channel не в шаблоне сервера
    mood: m.mood || "ok",
  }));

  // Агрегаты справа
  const total = meets.length;
  const withRisk = meets.filter(m => m.mood === "risk").length;
  const withOk = meets.filter(m => m.mood === "ok").length;
  return (
    <div>
      <TopBar breadcrumbs={["am hub","встречи"]} title="Встречи"
        subtitle={total > 0 ? `${total} предстоящих · ${withRisk} с риском · ${withOk} ок` : "Нет предстоящих встреч"}
        actions={<><Btn kind="ghost" size="m">Все</Btn><Btn kind="dim" size="m">Мои</Btn><Btn kind="primary" size="m" icon={<I.plus size={14}/>}>Запланировать</Btn></>}/>
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 320px", gap: 18 }}>
        <Card title="Расписание · предстоящие">
          {meets.length === 0 && (
            <div style={{ padding: "28px 10px", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
              Нет предстоящих встреч в вашем календаре.
            </div>
          )}
          {meets.map((m, i) => (
            <div key={i} style={{ display: "grid", gridTemplateColumns: "150px 1fr 140px 90px 40px", gap: 14, padding: "14px 6px", borderBottom: i===meets.length-1?"none":"1px solid var(--line-soft)", alignItems: "center" }}>
              <div>
                <div style={{ fontSize: 12.5, color: "var(--ink-9)", fontWeight: 500 }}>{m.d.split(" · ")[0]}</div>
                <div className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>{m.d.split(" · ")[1]}</div>
              </div>
              <div>
                <div style={{ fontSize: 13.5, color: "var(--ink-9)", fontWeight: 500 }}>{m.cl}</div>
                <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{m.kind} · {m.ch}</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Avatar name={m.who} size={22}/>
                <span style={{ fontSize: 12, color: "var(--ink-7)" }}>{m.who}</span>
              </div>
              <Seg value={m.seg}/>
              <I.arrow_r size={14} stroke="var(--ink-5)"/>
            </div>
          ))}
        </Card>

        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <Card title="Статистика · предстоящие" dense>
            {[
              { l: "Всего", v: total, total: Math.max(total, 1) },
              { l: "С риском", v: withRisk, total: Math.max(total, 1) },
              { l: "С позитивом", v: withOk, total: Math.max(total, 1) },
              { l: "Нейтральные", v: Math.max(0, total - withRisk - withOk), total: Math.max(total, 1) },
            ].map((s, i) => (
              <div key={i} style={{ padding: "10px 0", borderBottom: i===3?"none":"1px solid var(--line-soft)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                  <span className="mono" style={{ fontSize: 11, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{s.l}</span>
                  <span style={{ fontSize: 13, color: "var(--ink-8)", fontWeight: 500 }}>{s.v}<span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}> / {s.total}</span></span>
                </div>
                <Progress value={(s.v/s.total)*100} tone="signal" h={3}/>
              </div>
            ))}
          </Card>

          <Card title="Создать встречу" dense>
            {[
              { label: "Чекап · 30 мин",     type: "checkup",    dur: 30 },
              { label: "QBR · 60 мин",       type: "qbr",        dur: 60 },
              { label: "Sync · 15 мин",      type: "sync",       dur: 15 },
              { label: "Онбординг · 90 мин", type: "onboarding", dur: 90 },
              { label: "Эскалация",          type: "escalation", dur: 30 },
            ].map((t,i,a)=>(
              <div key={i} onClick={() => {
                const clientId = prompt("ID клиента для встречи «" + t.label + "»:");
                if (!clientId) return;
                fetch("/api/meetings", {
                  method: "POST", credentials: "include",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    client_id: parseInt(clientId, 10),
                    type: t.type,
                    title: t.label,
                    duration: t.dur,
                    date: new Date(Date.now() + 24*60*60*1000).toISOString(),
                  })
                }).then(r => r.ok ? r.json() : Promise.reject(r.statusText))
                  .then(() => { alert("Создано"); location.reload(); })
                  .catch(e => alert("Ошибка: " + e));
              }}
              style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 0", borderBottom: i === a.length - 1 ? "none" : "1px solid var(--line-soft)", cursor: "pointer" }}>
                <I.cal size={14} stroke="var(--ink-6)"/>
                <span style={{ flex: 1, fontSize: 12.5, color: "var(--ink-8)" }}>{t.label}</span>
                <I.arrow_r size={12} stroke="var(--ink-5)"/>
              </div>
            ))}
          </Card>
        </div>
      </div>
    </div>
  );
}

// ── Portfolio ─────────────────────────────────────────────
function PagePortfolio() {
  const CL = (typeof window !== "undefined" && window.CLIENTS) || [];

  // Группируем реальных клиентов по сегментам
  const segs = [
    { l: "ENT",  segs: ["ENT"],          t: "signal" },
    { l: "SME+", segs: ["SME+"],         t: "signal" },
    { l: "SME",  segs: ["SME", "SME-"],  t: "info" },
    { l: "SMB",  segs: ["SMB"],          t: "info" },
    { l: "SS",   segs: ["SS"],           t: "warn" },
    { l: "NEW",  segs: [""],             t: "ok" },    // без сегмента
  ].map(group => {
    const members = CL.filter(c => group.segs.includes((c.seg || "").toUpperCase()));
    const rub = members.reduce((s, c) => s + _pg(c.gmv), 0);
    return {
      ...group,
      n: members.length,
      v: rub >= 1_000_000 ? `₽ ${(rub/1_000_000).toFixed(1)}м`
         : rub >= 1000    ? `₽ ${Math.round(rub/1000)}к`
         : `₽ ${Math.round(rub)}`,
    };
  });

  // PM-распределение
  const pmMap = {};
  CL.forEach(c => {
    if (!c.pm || c.pm === "—") return;
    if (!pmMap[c.pm]) pmMap[c.pm] = { pm: c.pm, n: 0, r: 0 };
    pmMap[c.pm].n += 1;
    if (c.status === "risk") pmMap[c.pm].r += 1;
  });
  const pms = Object.values(pmMap).sort((a, b) => b.n - a.n).slice(0, 8);
  const maxPm = pms.reduce((m, p) => Math.max(m, p.n), 1);

  const totalGmv = CL.reduce((s, c) => s + _pg(c.gmv), 0);
  const totalFmt = totalGmv >= 1_000_000 ? `₽ ${(totalGmv/1_000_000).toFixed(1)}м` : `₽ ${Math.round(totalGmv/1000)}к`;

  return (
    <div>
      <TopBar breadcrumbs={["am hub","портфель"]} title="Портфель · структура"
        subtitle={`${CL.length} клиентов · ${totalFmt} · ${pms.length} ${pms.length === 1 ? "менеджер" : "менеджеров"}`}
        actions={<><Btn kind="ghost" size="m">По сегменту</Btn><Btn kind="dim" size="m">По менеджеру</Btn></>}/>
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 12 }}>
          {segs.map((s,i)=>(
            <div key={i} style={{ padding: 16, background: "var(--ink-2)", border: "1px solid var(--line)", borderLeft: `3px solid var(--${s.t})`, borderRadius: 6 }}>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>сегмент</div>
              <div style={{ fontSize: 28, fontWeight: 500, color: `var(--${s.t})`, letterSpacing: "-0.03em", marginTop: 4 }}>{s.l}</div>
              <div className="mono" style={{ fontSize: 12, color: "var(--ink-8)", marginTop: 6 }}>{s.n} {s.n === 1 ? "клиент" : "клиентов"}</div>
              <div className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>{s.v}</div>
            </div>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
          <Card title="Распределение по менеджерам">
            {pms.length === 0 && (
              <div style={{ padding: "22px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
                Нет данных о менеджерах клиентов.
              </div>
            )}
            {pms.map((p, i) => (
              <div key={i} style={{ padding: "14px 0", borderBottom: i===pms.length-1?"none":"1px solid var(--line-soft)", display: "grid", gridTemplateColumns: "36px 1fr 60px 90px 60px", gap: 12, alignItems: "center" }}>
                <Avatar name={p.pm}/>
                <div>
                  <div style={{ fontSize: 13, color: "var(--ink-9)", fontWeight: 500 }}>{p.pm}</div>
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>account manager</div>
                </div>
                <span className="mono" style={{ fontSize: 13, color: "var(--ink-8)" }}>{p.n}</span>
                <Progress value={(p.n / maxPm) * 100} tone={p.r > 0 ? "warn" : "signal"} h={3}/>
                {p.r > 0 ? <Badge tone="critical" dot>{p.r} risk</Badge> : <Badge tone="ok" dot>ok</Badge>}
              </div>
            ))}
          </Card>

          <Card title="Статус портфеля">
            {(function(){
              const risk = CL.filter(c=>c.status==="risk").length;
              const warn = CL.filter(c=>c.status==="warn").length;
              const ok   = CL.filter(c=>c.status==="ok").length;
              const total = CL.length || 1;
              const rows = [
                { label: "ок",         v: ok,   pct: Math.round(ok/total*100),   tone: "ok" },
                { label: "warn",       v: warn, pct: Math.round(warn/total*100), tone: "warn" },
                { label: "churn-риск", v: risk, pct: Math.round(risk/total*100), tone: "critical" },
              ];
              return <div style={{ display: "flex", flexDirection: "column", gap: 14, padding: "6px 0" }}>
                {rows.map((r, i) => (
                  <div key={i}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                      <span className="mono" style={{ fontSize: 11, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{r.label}</span>
                      <span style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-8)" }}>
                        {r.v}<span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}> · {r.pct}%</span>
                      </span>
                    </div>
                    <Progress value={r.pct} tone={r.tone} h={4}/>
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

// ── AI Assistant ──────────────────────────────────────────
function PageAI() {
  const [messages, setMessages] = React.useState([]);
  const [input, setInput] = React.useState("");
  const [sending, setSending] = React.useState(false);
  const [error, setError] = React.useState(null);
  const listRef = React.useRef(null);

  React.useEffect(() => {
    fetch("/api/ai/chat/history", { credentials: "include" })
      .then(r => r.ok ? r.json() : { messages: [] })
      .then(d => setMessages(d.messages || d || []))
      .catch(() => {});
  }, []);

  React.useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages]);

  const send = async (text) => {
    const msg = (text ?? input).trim();
    if (!msg || sending) return;
    setInput("");
    setError(null);
    const history = messages.map(m => ({ role: m.role, content: m.content || m.text }));
    setMessages(prev => [...prev, { role: "user", content: msg }]);
    setSending(true);
    try {
      const r = await fetch("/api/ai/chat", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg, history }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      setMessages(prev => [...prev, { role: "ai", content: d.reply || d.answer || "" }]);
    } catch (e) {
      setError(e.message || "Не удалось получить ответ");
    } finally {
      setSending(false);
    }
  };

  const newSession = () => { setMessages([]); setError(null); };

  const quickCommands = [
    { label: "Брифинг на завтра",   text: "Подготовь брифинг на завтра по моему портфелю" },
    { label: "Кто на churn-риске",  text: "Какие клиенты сейчас на churn-риске?" },
    { label: "Топ задач на неделю", text: "Какие задачи важнее всего сделать на этой неделе?" },
    { label: "Сводка по встречам",  text: "Дай сводку по предстоящим встречам на 7 дней" },
  ];

  const now = new Date();
  const dateLabel = now.toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const user = (typeof window !== "undefined" && window.__CURRENT_USER) || {};

  return (
    <div>
      <TopBar breadcrumbs={["am hub","ai-ассистент"]} title="AI-ассистент"
        subtitle="Чат с данными портфеля · авто-брифы · генерация follow-up"
        actions={<><Btn kind="primary" size="m" icon={<I.plus size={14}/>} onClick={newSession}>Новая сессия</Btn></>}/>
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 280px", gap: 18 }}>
        <Card title={`Диалог · ${dateLabel}`} action={<Badge tone="signal">data-grounded</Badge>}>
          <div ref={listRef} style={{ display: "flex", flexDirection: "column", gap: 14, maxHeight: 540, minHeight: 200, overflow: "auto" }}>
            {messages.length === 0 && (
              <div style={{ padding: "40px 20px", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
                Задайте первый вопрос — ассистент отвечает с учётом данных вашего портфеля.
              </div>
            )}
            {messages.map((m, i) => (
              <Msg key={i} role={m.role === "user" ? "user" : "ai"}>{m.content || m.text}</Msg>
            ))}
            {sending && <Msg role="ai"><span style={{ color: "var(--ink-5)" }}>печатает…</span></Msg>}
            {error && <div style={{ padding: 10, background: "color-mix(in oklch, var(--critical) 10%, transparent)", border: "1px solid color-mix(in oklch, var(--critical) 30%, transparent)", borderRadius: 6, color: "var(--critical)", fontSize: 12 }}>{error}</div>}
          </div>
          <form onSubmit={(e) => { e.preventDefault(); send(); }}
            style={{ display: "flex", gap: 8, marginTop: 14, padding: 10, background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 6 }}>
            <input value={input} onChange={(e) => setInput(e.target.value)} disabled={sending}
              placeholder="Спросите о портфеле, клиенте или задаче…"
              style={{ flex: 1, background: "transparent", border: 0, color: "var(--ink-8)", outline: "none", fontFamily: "var(--f-display)", fontSize: 13 }}/>
            <Btn size="s" kind="primary" type="submit" disabled={sending || !input.trim()} iconRight={<I.arrow_r size={12}/>}>Отправить</Btn>
          </form>
        </Card>

        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <Card title="Быстрые команды" dense>
            {quickCommands.map((c, i) => (
              <div key={i} onClick={() => send(c.text)}
                style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 0", borderBottom: i === quickCommands.length - 1 ? "none" : "1px solid var(--line-soft)", cursor: "pointer" }}>
                <I.spark size={12} stroke="var(--signal)"/>
                <span style={{ flex: 1, fontSize: 12.5, color: "var(--ink-8)" }}>{c.label}</span>
                <Kbd>↵</Kbd>
              </div>
            ))}
          </Card>

          <Card title="Контекст сессии" dense>
            <div style={{ fontSize: 12, color: "var(--ink-7)", lineHeight: 1.55 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}><span className="dim">менеджер</span><span className="mono" style={{ fontSize: 11 }}>{user.name || "—"}</span></div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}><span className="dim">клиентов</span><span className="mono">{(window.CLIENTS || []).length}</span></div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}><span className="dim">сообщений</span><span className="mono">{messages.length}</span></div>
              <div style={{ display: "flex", justifyContent: "space-between" }}><span className="dim">статус</span><span className="mono" style={{ color: sending ? "var(--warn)" : "var(--ok)" }}>{sending ? "ждёт ответа" : "готов"}</span></div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
function Msg({ role, children }) {
  const isUser = role === "user";
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "flex-start", flexDirection: isUser ? "row-reverse" : "row" }}>
      <div style={{ width: 24, height: 24, borderRadius: 4, background: isUser ? "var(--ink-3)" : "color-mix(in oklch, var(--signal) 18%, var(--ink-2))", color: isUser ? "var(--ink-7)" : "var(--signal)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
        {isUser ? <I.users size={13}/> : <I.bot size={13}/>}
      </div>
      <div style={{ maxWidth: "78%", padding: "10px 12px", background: isUser ? "var(--ink-3)" : "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 6, fontSize: 13, color: "var(--ink-8)", lineHeight: 1.55 }}>
        {children}
      </div>
    </div>
  );
}

// ── Kanban (using tasks layout) ───────────────────────────
function PageKanban() { return <PageTasks/>; }

// ── KPI ───────────────────────────────────────────────────
function PageKPI() {
  const CL = (typeof window !== "undefined" && window.CLIENTS) || [];
  const U  = (typeof window !== "undefined" && window.__CURRENT_USER) || {};
  const S  = (typeof window !== "undefined" && window.__SIDEBAR_STATS) || {};

  const totalGmv = CL.reduce((s, c) => s + _pg(c.gmv), 0);
  const gmvFmt = totalGmv >= 1_000_000 ? `₽ ${(totalGmv/1_000_000).toFixed(1)}м` : `₽ ${Math.round(totalGmv/1000)}к`;

  // Процент health>=ok из всего скоупа
  const okClients = CL.filter(c => c.status === "ok").length;
  const retention = CL.length > 0 ? Math.round((okClients / CL.length) * 100) : 0;

  // План GMV — 120% от текущего (как грубая оценка)
  const gmvPlan = Math.round(totalGmv * 1.2);
  const gmvPlanFmt = gmvPlan >= 1_000_000 ? `₽ ${(gmvPlan/1_000_000).toFixed(1)}м` : `₽ ${Math.round(gmvPlan/1000)}к`;

  const kpis = [
    { l: "GMV портфеля", v: gmvFmt, plan: gmvPlanFmt, pct: gmvPlan > 0 ? Math.round((totalGmv/gmvPlan)*100) : 0, tone: "ok" },
    { l: "Клиентов ok",  v: String(okClients), plan: String(CL.length), pct: CL.length > 0 ? Math.round((okClients/CL.length)*100) : 0, tone: "signal" },
    { l: "Retention",    v: `${retention}%`, plan: "92%", pct: retention > 92 ? 100 : Math.round((retention/92)*100), tone: retention >= 92 ? "signal" : "warn" },
    { l: "Активных задач", v: String(S.tasksActive || 0), plan: "—", pct: 0, tone: "info" },
  ];

  return (
    <div>
      <TopBar breadcrumbs={["am hub","мой kpi"]} title="Мой KPI"
        subtitle={`${U.name || U.email || "Менеджер"} · ${U.role || "user"}`}/>
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12 }}>
          {kpis.map((k,i)=>(
            <div key={i} style={{ padding: 18, background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6 }}>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{k.l}</div>
              <div style={{ fontSize: 34, fontWeight: 500, color: `var(--${k.tone})`, letterSpacing: "-0.03em", lineHeight: 1, marginTop: 8 }}>{k.v}</div>
              <div className="mono" style={{ fontSize: 11, color: "var(--ink-6)", marginTop: 4 }}>цель · {k.plan}</div>
              <div style={{ marginTop: 12 }}><Progress value={k.pct} tone={k.tone} h={4}/></div>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-6)", marginTop: 4 }}>{k.pct}% от плана</div>
            </div>
          ))}
        </div>

        <Card title="Прогресс · по неделям">
          {(function(){
            const weekly = (typeof window !== "undefined" && window.KPI_WEEKLY) || [];
            if (!weekly.length) {
              return <div style={{ padding: "30px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
                Данных о недельном прогрессе пока нет.
              </div>;
            }
            const maxV = Math.max(1, ...weekly.map(w => w.value || 0));
            return <div style={{ display: "grid", gridTemplateColumns: `repeat(${weekly.length},1fr)`, gap: 4, alignItems: "end", height: 160 }}>
              {weekly.map((w, i) => {
                const h = Math.max(4, Math.round((w.value || 0) / maxV * 140));
                return (
                  <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
                    <div style={{ width: "100%", height: h, background: w.active ? "var(--signal)" : "var(--ink-3)", borderRadius: "2px 2px 0 0" }} title={String(w.value || 0)}/>
                    <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)" }}>{w.label || `W${i+1}`}</span>
                  </div>
                );
              })}
            </div>;
          })()}
        </Card>
      </div>
    </div>
  );
}

// ── Cabinet ───────────────────────────────────────────────
function PageCabinet() {
  return (
    <div>
      <TopBar breadcrumbs={["am hub","мой кабинет"]} title="Мой кабинет" subtitle="Личные материалы, заметки и документы"
        actions={<Btn kind="primary" size="m" icon={<I.plus size={14}/>}>Загрузить</Btn>}/>
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "220px 1fr", gap: 18 }}>
        <Card title="Папки">
          {(function(){
            const folders = (typeof window !== "undefined" && window.CABINET_FOLDERS) || [];
            if (!folders.length) {
              return <div style={{ padding: "16px 0", color: "var(--ink-6)", fontSize: 12, textAlign: "center" }}>
                Нет папок.
              </div>;
            }
            return folders.map((f,i)=>{
              const Ic = I[f.icon || f.i] || I.folder;
              return (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 6px", borderBottom: i === folders.length - 1 ? "none" : "1px solid var(--line-soft)", cursor: "pointer" }}>
                  <Ic size={14} stroke="var(--ink-6)"/>
                  <span style={{ flex: 1, fontSize: 12.5, color: "var(--ink-8)" }}>{f.name || f.n}</span>
                  <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>{f.count ?? f.c ?? 0}</span>
                </div>
              );
            });
          })()}
        </Card>

        <Card title="Недавние файлы" action={<Btn size="s" kind="ghost" icon={<I.grid size={12}/>}>Grid</Btn>}>
          {(function(){
            const files = (typeof window !== "undefined" && window.RECENT_FILES) || [];
            if (!files.length) {
              return <div style={{ padding: "30px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
                Файлов пока нет. Загрузите первый документ кнопкой выше.
              </div>;
            }
            return <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 14 }}>
              {files.map((f,i)=>(
                <div key={i} style={{ background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
                  <Placeholder h={90} label={f.type || f.t}/>
                  <div style={{ padding: 10 }}>
                    <div style={{ fontSize: 12.5, color: "var(--ink-8)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name || f.n}</div>
                    <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", marginTop: 3 }}>{f.date || f.d}</div>
                  </div>
                </div>
              ))}
            </div>;
          })()}
        </Card>
      </div>
    </div>
  );
}

// ── Templates ─────────────────────────────────────────────
function PageTemplates() {
  const tpls = (typeof window !== "undefined" && window.TEMPLATES) || [];
  return (
    <div>
      <TopBar breadcrumbs={["am hub","шаблоны"]} title="Шаблоны" subtitle="Follow-up, чекапы, QBR — шаблоны общения с клиентами"
        actions={<Btn kind="primary" size="m" icon={<I.plus size={14}/>}>Новый шаблон</Btn>}/>
      <div style={{ padding: "22px 28px 40px" }}>
        {tpls.length === 0 && (
          <div style={{ padding: "40px 20px", color: "var(--ink-6)", textAlign: "center", fontSize: 13, background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6 }}>
            Шаблонов пока нет. Создайте первый, нажав «Новый шаблон».
          </div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 14 }}>
        {tpls.map((t,i)=>(
          <div key={i} style={{ background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6, padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 500, color: "var(--ink-9)" }}>{t.name || t.n}</div>
                <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", marginTop: 3 }}>{t.category || t.cat}</div>
              </div>
              <Badge tone="ghost">{(t.usage ?? 0)}×</Badge>
            </div>
            <div style={{ color: "var(--ink-6)", lineHeight: 1.5, padding: 10, background: "var(--ink-1)", border: "1px solid var(--line-soft)", borderRadius: 4, fontFamily: "var(--f-mono)", fontSize: 11, whiteSpace: "pre-wrap", overflow: "hidden", maxHeight: 70 }}>{t.body || ""}</div>
            <div style={{ display: "flex", gap: 6 }}>
              <Btn size="s" kind="ghost">Превью</Btn>
              <Btn size="s" kind="dim">Применить</Btn>
            </div>
          </div>
        ))}
        </div>
      </div>
    </div>
  );
}

// ── Auto tasks ────────────────────────────────────────────
function PageAuto() {
  const rules = (typeof window !== "undefined" && window.AUTO_RULES) || [];
  const stats = (typeof window !== "undefined" && window.AUTO_STATS) || {};
  const activeCount = rules.filter(r => r.on).length;
  return (
    <div>
      <TopBar breadcrumbs={["am hub","автозадачи"]} title="Автозадачи"
        subtitle="Правила `IF-THEN`: когда система создаёт задачи автоматически"
        actions={<Btn kind="primary" size="m" icon={<I.plus size={14}/>}>Новое правило</Btn>}/>
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12 }}>
          <KPI label="Правил активно" value={String(activeCount)} unit={`/ ${rules.length}`}/>
          <KPI label="Создано задач · 30д" value={String(stats.tasks_30d ?? 0)} tone="signal" delta={stats.tasks_30d_delta}/>
          <KPI label="Ср. время реакции" value={String(stats.avg_reaction_min ?? "—")} unit={stats.avg_reaction_min != null ? "минут" : ""} tone="ok" delta={stats.avg_reaction_delta}/>
        </div>

        <Card title="Правила">
          {rules.length === 0 && (
            <div style={{ padding: "30px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
              Автоправил пока нет. Нажмите «Новое правило», чтобы создать первое.
            </div>
          )}
          {rules.map((r,i)=>(
            <div key={i} style={{ display: "grid", gridTemplateColumns: "48px 1fr 1fr 80px 40px", gap: 14, padding: "14px 6px", borderBottom: i===rules.length-1?"none":"1px solid var(--line-soft)", alignItems: "center" }}>
              <label style={{ display: "inline-flex", alignItems: "center" }}>
                <span style={{ width: 32, height: 18, background: r.on?"var(--signal)":"var(--ink-4)", borderRadius: 999, position: "relative", cursor: "pointer" }}>
                  <span style={{ position: "absolute", top: 2, left: r.on?16:2, width: 14, height: 14, background: "var(--ink-0)", borderRadius: 999, transition: "left 160ms var(--ease)" }}/>
                </span>
              </label>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Badge tone="warn">IF</Badge>
                <span style={{ fontSize: 13, color: "var(--ink-8)" }}>{r.trig}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Badge tone="signal">THEN</Badge>
                <span style={{ fontSize: 13, color: "var(--ink-8)" }}>{r.then}</span>
              </div>
              <span className="mono" style={{ fontSize: 12, color: r.hits>0?"var(--ink-7)":"var(--ink-5)", textAlign: "right" }}>{r.hits}×</span>
              <I.dot3 size={14} stroke="var(--ink-6)"/>
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
}

// ── Roadmap ───────────────────────────────────────────────
function PageRoadmap() {
  const rawCols = (typeof window !== "undefined" && window.ROADMAP) || [];
  const U = (typeof window !== "undefined" && window.__CURRENT_USER) || {};
  const isAdmin = (U.role || "") === "admin";

  // Фиксированные колонки (даже если БД пустая — показываем все 5)
  const DEFAULT = [
    { key: "q1",      title: "Q1 · готово",   tone: "ok" },
    { key: "q2",      title: "Q2 · в работе", tone: "signal" },
    { key: "q3",      title: "Q3 · план",     tone: "info" },
    { key: "q4",      title: "Q4 · идеи",     tone: "warn" },
    { key: "backlog", title: "Бэклог",        tone: "neutral" },
  ];
  const byKey = Object.fromEntries(rawCols.map(c => [c.key || c.column_key, c]));
  const cols = DEFAULT.map(d => ({ ...d, ...(byKey[d.key] || {}), items: (byKey[d.key]?.items) || [] }));

  const addItem = (col) => {
    const title = prompt(`Новый пункт в «${col.title}»:`);
    if (!title) return;
    fetch("/design/api/roadmap", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        column_key: col.key, column_title: col.title,
        tone: col.tone, title,
      })
    }).then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(() => location.reload())
      .catch(e => alert("Ошибка: " + e));
  };

  const delItem = (id, title) => {
    if (!confirm(`Удалить «${title}»?`)) return;
    fetch(`/design/api/roadmap/${id}`, { method: "DELETE", credentials: "include" })
      .then(r => r.ok ? location.reload() : alert("Ошибка удаления"));
  };

  return (
    <div>
      <TopBar breadcrumbs={["am hub","роадмап"]} title="Роадмап"
        subtitle={`Что команда строит в AM Hub · ${new Date().getFullYear()}`}/>
      <div style={{ padding: "22px 28px 40px" }}>
        {!isAdmin && rawCols.length === 0 && (
          <div style={{ padding: "20px", color: "var(--ink-6)", textAlign: "center", fontSize: 13, background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6, marginBottom: 14 }}>
            Роадмап пока пуст. Пункты добавляют администраторы.
          </div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: `repeat(${cols.length},1fr)`, gap: 14 }}>
          {cols.map((c, i) => (
            <div key={c.key} style={{ background: "var(--ink-2)", border: "1px solid var(--line)", borderTop: `3px solid var(--${c.tone})`, borderRadius: "0 0 6px 6px", padding: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <div className="mono" style={{ fontSize: 11, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.1em" }}>{c.title}</div>
                {isAdmin && (
                  <button onClick={() => addItem(c)} title="Добавить"
                    style={{ background: "transparent", border: "1px solid var(--line)", color: "var(--ink-7)", width: 22, height: 22, borderRadius: 3, cursor: "pointer", fontSize: 14, lineHeight: 1, padding: 0 }}>+</button>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {c.items.map((it, j) => (
                  <div key={j} style={{ padding: 10, background: "var(--ink-1)", border: "1px solid var(--line-soft)", borderRadius: 4, display: "flex", alignItems: "center", gap: 8 }}>
                    <div style={{ flex: 1, fontSize: 12.5, color: "var(--ink-8)" }}>{typeof it === "string" ? it : (it.title || it.name)}</div>
                    {isAdmin && it.id && (
                      <button onClick={() => delItem(it.id, it.title)} title="Удалить"
                        style={{ background: "transparent", border: 0, color: "var(--ink-5)", cursor: "pointer", padding: 2, fontSize: 12 }}>✕</button>
                    )}
                  </div>
                ))}
                {c.items.length === 0 && (
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", fontStyle: "italic" }}>пусто</div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Internal tasks ────────────────────────────────────────
function PageInternal() {
  return (
    <div>
      <TopBar breadcrumbs={["am hub","внутренние задачи"]} title="Внутренние задачи"
        subtitle="Задачи команды без привязки к клиенту"
        actions={<Btn kind="primary" size="m" icon={<I.plus size={14}/>}>Задача</Btn>}/>
      <div style={{ padding: "22px 28px 40px" }}>
        <Card title="Задачи команды">
          {(function(){
            const items = (typeof window !== "undefined" && window.INTERNAL_TASKS) || [];
            if (!items.length) {
              return <div style={{ padding: "30px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
                Внутренних задач пока нет.
              </div>;
            }
            return items.map((r,i,a)=>{
              const pr = r.priority || r.pr || "low";
              return (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "20px 1fr 180px 80px 80px", gap: 14, padding: "12px 6px", borderBottom: i===a.length-1?"none":"1px solid var(--line-soft)", alignItems: "center" }}>
                  <input type="checkbox" defaultChecked={!!r.done} style={{ accentColor: "var(--signal)" }}/>
                  <span style={{ fontSize: 13, color: "var(--ink-8)" }}>{r.title || r.t}</span>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}><Avatar name={r.owner || "—"} size={20}/><span style={{ fontSize: 12, color: "var(--ink-7)" }}>{r.owner || "—"}</span></div>
                  <span className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>{r.due || "—"}</span>
                  <Badge tone={pr==="high"?"warn":pr==="med"?"info":"neutral"} dot>{pr}</Badge>
                </div>
              );
            });
          })()}
        </Card>
      </div>
    </div>
  );
}

// ── Extension install page ────────────────────────────────
function PageExtInstall() {
  const EXTS   = (typeof window !== "undefined" && window.__EXTENSIONS) || [];
  const HUB    = (typeof window !== "undefined" && window.__HUB_URL)    || window.location.origin;

  // Простой clipboard helper с fallback
  function _copy(text, label) {
    try {
      navigator.clipboard.writeText(text);
      alert((label || "Скопировано") + ": " + text);
    } catch (e) {
      prompt("Скопируйте вручную:", text);
    }
  }

  return (
    <div>
      <TopBar breadcrumbs={["am hub","расширение"]} title="Расширения браузера"
        subtitle={`${EXTS.length} ${EXTS.length === 1 ? "расширение" : "расширения"} · скачать, установить, настроить`}/>
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 380px", gap: 28 }}>

        {/* ── LEFT: карточки расширений ───────────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>

          {EXTS.map((ext, i) => (
            <Card
              key={ext.id}
              title={<span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
                {ext.name}
                {ext.primary && <Badge tone="signal" dot>основное</Badge>}
                {ext.auto_update && <Badge tone="info">auto-update</Badge>}
              </span>}
              action={<span className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>v {ext.version}</span>}
            >
              <div style={{ fontSize: 13, color: "var(--ink-7)", lineHeight: 1.5, marginBottom: 14 }}>
                {ext.description}
              </div>

              {/* download кнопки */}
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                {ext.crx_url && (
                  <a href={ext.crx_url} download style={{ textDecoration: "none" }}>
                    <Btn kind="primary" size="m" icon={<I.download size={14}/>}>
                      Скачать .crx ({ext.crx_size_kb} KB)
                    </Btn>
                  </a>
                )}
                {ext.zip_url && (
                  <a href={ext.zip_url} download style={{ textDecoration: "none" }}>
                    <Btn kind={ext.crx_url ? "ghost" : "primary"} size="m" icon={<I.download size={14}/>}>
                      Скачать .zip ({ext.zip_size_kb} KB)
                    </Btn>
                  </a>
                )}
              </div>

              {ext.extension_id && (
                <div style={{ marginTop: 14, padding: 10, background: "var(--ink-1)", border: "1px dashed var(--line)", borderRadius: 4, display: "flex", alignItems: "center", gap: 10 }}>
                  <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>id</span>
                  <code className="mono" style={{ fontSize: 11, color: "var(--ink-7)", flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>{ext.extension_id}</code>
                  <button onClick={() => _copy(ext.extension_id, "Extension ID")}
                    style={{ background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 3, padding: "3px 8px", cursor: "pointer", color: "var(--ink-7)", fontSize: 11 }}>
                    <I.copy size={11}/>
                  </button>
                </div>
              )}
            </Card>
          ))}

          {EXTS.length === 0 && (
            <Card title="Нет расширений">
              <div style={{ padding: "20px 0", color: "var(--ink-6)", fontSize: 13 }}>
                Метаданные расширений не загружены.
              </div>
            </Card>
          )}

          {/* Install steps — Load unpacked из .zip (Chrome блокирует drag-drop .crx
              без подписи Web Store, поэтому единственный надёжный путь для
              внутреннего расширения — Developer mode + Load unpacked) */}
          <Card title="Как установить · 4 шага">
            {[
              { s: "01", t: "Скачать .zip расширения", sub: "кнопка выше" },
              { s: "02", t: "Распаковать архив в любую папку", sub: "не удаляйте её после установки" },
              { s: "03", t: "Открыть chrome://extensions и включить Developer mode", sub: "тумблер в правом верхнем углу" },
              { s: "04", t: "Load unpacked → выбрать распакованную папку", sub: "иконка появится в тулбаре" },
            ].map((step, i, a) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "48px 1fr", gap: 14, padding: "14px 0", borderBottom: i === a.length - 1 ? "none" : "1px solid var(--line-soft)", alignItems: "flex-start" }}>
                <span className="mono" style={{ fontSize: 22, color: "var(--signal)", fontWeight: 500 }}>{step.s}</span>
                <div>
                  <div style={{ fontSize: 14, color: "var(--ink-9)", fontWeight: 500 }}>{step.t}</div>
                  <div className="mono" style={{ fontSize: 11, color: "var(--ink-5)", marginTop: 3 }}>{step.sub}</div>
                </div>
              </div>
            ))}
            <div style={{ marginTop: 12, padding: 12, background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 4, display: "flex", alignItems: "center", gap: 10 }}>
              <I.link size={14} stroke="var(--signal)"/>
              <span style={{ flex: 1, fontSize: 13, color: "var(--ink-7)" }}>Открыть <code className="mono" style={{ color: "var(--ink-9)" }}>chrome://extensions</code></span>
              <button onClick={() => _copy("chrome://extensions", "URL")}
                style={{ background: "var(--ink-3)", border: "1px solid var(--line)", borderRadius: 3, padding: "4px 10px", cursor: "pointer", color: "var(--ink-7)", fontSize: 11 }}>
                скопировать
              </button>
            </div>
          </Card>

          {/* Что внутри — модули единого расширения */}
          <Card title="Модули внутри AM Hub">
            {[
              { i: "refresh", t: "Sync — Merchrules → AM Hub, каждые 30 минут и по кнопке" },
              { i: "check",   t: "Checkup — автоматический анализ качества поиска Diginetica" },
              { i: "lock",    t: "Tokens — перехват сессий Ktalk и T-Bank Time для API" },
              { i: "bell",    t: "Уведомления при критических изменениях" },
            ].map((r,i)=>{const Ic = I[r.i] || I.circle_check; return (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 0", borderBottom: i===3?"none":"1px solid var(--line-soft)" }}>
                <div style={{ width: 28, height: 28, borderRadius: 4, background: "var(--ink-1)", border: "1px solid var(--line)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--signal)" }}><Ic size={14}/></div>
                <span style={{ fontSize: 13, color: "var(--ink-8)" }}>{r.t}</span>
              </div>
            );})}
          </Card>
        </div>

        {/* ── RIGHT: настройки для вставки + preview ──────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>

          <Card title="Настройки для popup" action={<span className="mono" style={{ fontSize: 10, color: "var(--ink-5)" }}>вставить в поля расширения</span>}>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>

              <div>
                <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>AM Hub · URL</div>
                <div style={{ display: "flex", gap: 6 }}>
                  <input readOnly value={HUB || window.location.origin}
                    style={{ flex: 1, padding: "8px 10px", background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-9)", fontFamily: "var(--f-mono)", fontSize: 12, outline: "none" }}/>
                  <Btn size="s" kind="ghost" icon={<I.copy size={12}/>} onClick={() => _copy(HUB || window.location.origin, "Hub URL")}>копия</Btn>
                </div>
              </div>

              <div>
                <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>AM Hub · токен</div>
                <div style={{ padding: 10, background: "var(--ink-1)", border: "1px dashed var(--line)", borderRadius: 4, fontSize: 12, color: "var(--ink-7)", lineHeight: 1.5 }}>
                  Токен берётся из cookie <code className="mono" style={{ color: "var(--ink-9)" }}>auth_token</code> этого браузера
                  при использовании Hub-API. Для расширения — сгенерируйте отдельный API-токен в
                  разделе <span style={{ color: "var(--signal)" }}>Мой кабинет → API</span> (скоро).
                </div>
              </div>

              <div>
                <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Merchrules</div>
                <div style={{ padding: 10, background: "var(--ink-1)", border: "1px dashed var(--line)", borderRadius: 4, fontSize: 12, color: "var(--ink-7)", lineHeight: 1.5 }}>
                  Логин и пароль Merchrules — свои индивидуальные. Расширение хранит их локально
                  и использует только для синхронизации с вашим AM Hub.
                </div>
              </div>

            </div>
          </Card>

          <Card title="Живой превью popup">
            {typeof window !== "undefined" && window.ExtensionPopup
              ? <ExtensionPopup state="connected"/>
              : <div style={{ padding: 20, color: "var(--ink-6)", fontSize: 12, textAlign: "center" }}>превью недоступно</div>}
          </Card>
        </div>

      </div>
    </div>
  );
}

// ── Help ──────────────────────────────────────────────────
function PageHelp() {
  return (
    <div>
      <TopBar breadcrumbs={["am hub","помощь"]} title="Помощь и документация"
        subtitle="Как пользоваться AM Hub эффективно"/>
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
        {[
          { t: "Быстрый старт", sub: "7 шагов · 10 мин", topics: ["Создать первого клиента","Запланировать чекап","Отправить follow-up","Настроить уведомления"] },
          { t: "AI-ассистент",   sub: "паттерны промптов", topics: ["Бриф перед встречей","Экстренная генерация","Команды-ярлыки","Контекстные запросы"] },
          { t: "Интеграции",     sub: "подключения",      topics: ["Merchrules → AM Hub","KTalk · встречи","Airtable · импорт","Telegram · бот"] },
          { t: "Горячие клавиши", sub: "всё быстрее",     topics: ["⌘ K — поиск","⌘ N — новая задача","⌘ / — заметка","⌘ ⇧ D — дайджест"] },
          { t: "Отчётность",     sub: "аналитика",        topics: ["Портфель в PDF","Экспорт в Excel","Custom dashboards","Публичные ссылки"] },
          { t: "FAQ",            sub: "частые вопросы",   topics: ["Сброс пароля","Миграция с Excel","Оффлайн-режим","Защита данных"] },
        ].map((s,i)=>(
          <div key={i} style={{ background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6, padding: 18 }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 12 }}>
              <div style={{ fontSize: 15, color: "var(--ink-9)", fontWeight: 500 }}>{s.t}</div>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>{s.sub}</div>
            </div>
            <div style={{ display: "flex", flexDirection: "column" }}>
              {s.topics.map((tp,j)=>(
                <a key={j} style={{ padding: "8px 0", fontSize: 12.5, color: "var(--ink-7)", borderBottom: j===s.topics.length-1?"none":"1px solid var(--line-soft)", display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer" }}>
                  {tp}
                  <I.arrow_r size={12} stroke="var(--ink-5)"/>
                </a>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { PageTop50, PageTasks, PageMeetings, PagePortfolio, PageAI, PageKanban, PageKPI, PageCabinet, PageTemplates, PageAuto, PageRoadmap, PageInternal, PageExtInstall, PageHelp });
