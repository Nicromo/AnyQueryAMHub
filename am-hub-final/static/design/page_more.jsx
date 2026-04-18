// page_more.jsx — remaining tabs (top50, tasks, meetings, portfolio, ai, kanban, kpi, cabinet, templates, auto, roadmap, internal, extension-install, help)

// ── Top-50 ────────────────────────────────────────────────
function PageTop50() {
  const rows = [
    { rk: 1, name: "СтройМаркет-21", seg: "A+", gmv: "₽ 4.8м", growth: "+12%", health: 72, pm: "Анна С." },
    { rk: 2, name: "Retail Group PRO", seg: "A+", gmv: "₽ 4.2м", growth: "+22%", health: 88, pm: "Кирилл В." },
    { rk: 3, name: "ТехноЛайн", seg: "A", gmv: "₽ 2.1м", growth: "+4%", health: 81, pm: "Анна С." },
    { rk: 4, name: "Aura Beauty", seg: "A", gmv: "₽ 3.1м", growth: "−18%", health: 42, pm: "Анна С." },
    { rk: 5, name: "Gemini Shop", seg: "B+", gmv: "₽ 1.6м", growth: "−3%", health: 64, pm: "Анна С." },
    { rk: 6, name: "Orbita Marketplace", seg: "A", gmv: "₽ 2.8м", growth: "+8%", health: 90, pm: "Лиза М." },
    { rk: 7, name: "Nextfood Retail", seg: "B+", gmv: "₽ 1.4м", growth: "−12%", health: 38, pm: "Павел Р." },
    { rk: 8, name: "Моя Полка", seg: "B", gmv: "₽ 1.1м", growth: "+8%", health: 76, pm: "Анна С." },
    { rk: 9, name: "Kitchen Garden", seg: "B", gmv: "₽ 0.9м", growth: "+22%", health: 70, pm: "Анна С." },
    { rk: 10, name: "Fiori Shop", seg: "B", gmv: "₽ 0.8м", growth: "−5%", health: 52, pm: "Лиза М." },
  ];
  return (
    <div>
      <TopBar breadcrumbs={["am hub","top-50"]} title="Top-50 · приоритетный портфель"
        subtitle="Клиенты, формирующие 78% GMV команды"
        actions={<><Btn kind="ghost" size="m" icon={<I.filter size={14}/>}>Фильтр</Btn><Btn kind="primary" size="m" icon={<I.download size={14}/>}>PDF-отчёт</Btn></>}/>
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12 }}>
          <KPI label="Top-50 · GMV" value="₽ 134м" delta="+14%" sub="из ₽ 174м всего" big/>
          <KPI label="Средний health" value="71" tone="warn" delta="−3"/>
          <KPI label="Под риском" value="8" tone="critical" sub="из 50"/>
          <KPI label="Рост >10%" value="23" tone="ok" sub="клиентов"/>
        </div>

        <Card title="Рейтинг · апрель 2026">
          <div style={{ background: "var(--ink-2)", borderRadius: 4 }}>
            <div style={{ display: "grid", gridTemplateColumns: "50px 1.6fr 70px 110px 90px 1fr 110px", gap: 14, padding: "10px 10px", background: "var(--ink-1)", borderRadius: 4, fontFamily: "var(--f-mono)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--ink-5)" }}>
              <span>#</span><span>клиент</span><span>seg</span><span>gmv 30д</span><span>Δ</span><span>health</span><span>am</span>
            </div>
            {rows.map((r, i) => (
              <div key={r.rk} style={{ display: "grid", gridTemplateColumns: "50px 1.6fr 70px 110px 90px 1fr 110px", gap: 14, padding: "12px 10px", alignItems: "center", borderBottom: i===rows.length-1?"none":"1px solid var(--line-soft)" }}>
                <span className="mono" style={{ fontSize: 13, fontWeight: 500, color: r.rk <= 3 ? "var(--signal)" : "var(--ink-6)" }}>{String(r.rk).padStart(2,"0")}</span>
                <span style={{ fontSize: 13, color: "var(--ink-9)", fontWeight: 500 }}>{r.name}</span>
                <Seg value={r.seg}/>
                <span className="mono" style={{ fontSize: 12, color: "var(--ink-8)" }}>{r.gmv}</span>
                <span className="mono" style={{ fontSize: 12, color: r.growth.startsWith("−") ? "var(--critical)" : "var(--ok)", fontWeight: 500 }}>{r.growth}</span>
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
  const cols = [
    { title: "Бэклог", tone: "neutral", count: 14, items: [
      { t: "Согласовать условия Q3", cl: "Orbita", pr: "med" },
      { t: "Добавить метрики конверсии", cl: "Dostavka Pro", pr: "low" },
      { t: "Ротация API ключа", cl: "Vivo Tea", pr: "low" },
    ]},
    { title: "Сегодня", tone: "signal", count: 8, items: [
      { t: "Поднять лимит вывода", cl: "СтройМаркет-21", pr: "high" },
      { t: "Отправить follow-up", cl: "ТехноЛайн", pr: "med" },
      { t: "QBR подготовка", cl: "Aura Beauty", pr: "high" },
    ]},
    { title: "В работе", tone: "warn", count: 6, items: [
      { t: "Исследовать воронку оплаты", cl: "Aura Beauty", pr: "critical" },
      { t: "Запрос договора", cl: "Kitchen Garden", pr: "med" },
    ]},
    { title: "Готово · 7д", tone: "ok", count: 23, items: [
      { t: "Онбординг завершён", cl: "Моя Полка", pr: "med" },
      { t: "KPI обновлены", cl: "Raduga Mall", pr: "low" },
      { t: "Договор подписан", cl: "Lumen", pr: "high" },
    ]},
  ];
  return (
    <div>
      <TopBar breadcrumbs={["am hub","задачи"]} title="Задачи · канбан" subtitle="37 активных · 3 просрочено · 23 закрыто за неделю"
        actions={<><Btn kind="ghost" size="m">Мои</Btn><Btn kind="dim" size="m">Вся команда</Btn><Btn kind="primary" size="m" icon={<I.plus size={14}/>}>Задача</Btn></>}/>
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
  const meets = [
    { d: "сегодня · 14:00", cl: "СтройМаркет-21", kind: "checkup",   seg: "A+", who: "Ольга Ларина", ch: "KTalk",   mood: "risk" },
    { d: "сегодня · 16:30", cl: "ТехноЛайн",      kind: "QBR",       seg: "A",  who: "Дмитрий К.",  ch: "KTalk",   mood: "ok" },
    { d: "завтра · 10:00",  cl: "Kitchen Garden", kind: "onboarding",seg: "B",  who: "Ирина Н.",    ch: "Zoom",    mood: "warn" },
    { d: "завтра · 15:00",  cl: "Aura Beauty",    kind: "urgent",    seg: "A",  who: "Максим Р.",   ch: "KTalk",   mood: "risk" },
    { d: "20 апр · 11:00",  cl: "Gemini Shop",    kind: "checkup",   seg: "B+", who: "Виктор Л.",   ch: "оффлайн", mood: "ok" },
    { d: "22 апр · 14:30",  cl: "Orbita Mkt",     kind: "qbr-auto",  seg: "A",  who: "—",           ch: "авто",    mood: "info" },
    { d: "23 апр · 09:00",  cl: "Моя Полка",      kind: "checkup",   seg: "B",  who: "Игорь П.",    ch: "KTalk",   mood: "ok" },
  ];
  return (
    <div>
      <TopBar breadcrumbs={["am hub","встречи"]} title="Встречи" subtitle="Календарь всех чекапов, QBR и экстренных встреч"
        actions={<><Btn kind="ghost" size="m">Все</Btn><Btn kind="dim" size="m">Мои</Btn><Btn kind="primary" size="m" icon={<I.plus size={14}/>}>Запланировать</Btn></>}/>
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 320px", gap: 18 }}>
        <Card title="Расписание · неделя 16">
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
          <Card title="Статистика · квартал" dense>
            {[
              { l: "Проведено", v: 129, total: 148 },
              { l: "Отменено", v: 12, total: 148 },
              { l: "С риском", v: 38, total: 129 },
              { l: "С положительным", v: 74, total: 129 },
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

          <Card title="Шаблоны встреч" dense>
            {["30-мин чекап", "60-мин QBR", "15-мин sync", "онбординг · 90 мин", "эскалация"].map((t,i)=>(
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 0", borderBottom: i===4?"none":"1px solid var(--line-soft)" }}>
                <I.cal size={14} stroke="var(--ink-6)"/>
                <span style={{ flex: 1, fontSize: 12.5, color: "var(--ink-8)" }}>{t}</span>
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
  return (
    <div>
      <TopBar breadcrumbs={["am hub","портфель"]} title="Портфель · структура"
        subtitle="248 клиентов · ₽ 174м · 4 менеджера"
        actions={<><Btn kind="ghost" size="m">По сегменту</Btn><Btn kind="dim" size="m">По менеджеру</Btn></>}/>
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 12 }}>
          {[
            { l: "A+", n: 18, v: "₽ 48м", t: "signal" },
            { l: "A",  n: 46, v: "₽ 62м", t: "signal" },
            { l: "B+", n: 62, v: "₽ 34м", t: "info" },
            { l: "B",  n: 71, v: "₽ 21м", t: "info" },
            { l: "C",  n: 38, v: "₽ 7.4м", t: "warn" },
            { l: "NEW",n: 13, v: "₽ 1.6м", t: "ok" },
          ].map((s,i)=>(
            <div key={i} style={{ padding: 16, background: "var(--ink-2)", border: "1px solid var(--line)", borderLeft: `3px solid var(--${s.t})`, borderRadius: 6 }}>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>сегмент</div>
              <div style={{ fontSize: 32, fontWeight: 500, color: `var(--${s.t})`, letterSpacing: "-0.03em", marginTop: 4 }}>{s.l}</div>
              <div className="mono" style={{ fontSize: 12, color: "var(--ink-8)", marginTop: 6 }}>{s.n} клиентов</div>
              <div className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>{s.v}</div>
            </div>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
          <Card title="Распределение по менеджерам">
            {[
              { pm: "Анна Соколова",  n: 72, r: 3, tone: "signal" },
              { pm: "Кирилл Воронов", n: 64, r: 1, tone: "info" },
              { pm: "Лиза Морозова",  n: 58, r: 0, tone: "ok" },
              { pm: "Павел Ремнёв",   n: 54, r: 4, tone: "warn" },
            ].map((p,i)=>(
              <div key={i} style={{ padding: "14px 0", borderBottom: i===3?"none":"1px solid var(--line-soft)", display: "grid", gridTemplateColumns: "36px 1fr 60px 90px 60px", gap: 12, alignItems: "center" }}>
                <Avatar name={p.pm}/>
                <div>
                  <div style={{ fontSize: 13, color: "var(--ink-9)", fontWeight: 500 }}>{p.pm}</div>
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>tier-1 · senior</div>
                </div>
                <span className="mono" style={{ fontSize: 13, color: "var(--ink-8)" }}>{p.n}</span>
                <Progress value={p.n/0.72} tone={p.tone} h={3}/>
                {p.r > 0 ? <Badge tone="critical" dot>{p.r} risk</Badge> : <Badge tone="ok" dot>ok</Badge>}
              </div>
            ))}
          </Card>

          <Card title="Churn-сигналы · 90 дней">
            <div style={{ display: "grid", gridTemplateColumns: "repeat(12,1fr)", gap: 2, marginBottom: 12 }}>
              {Array.from({length: 90}).map((_, i) => {
                const v = ((i*17) % 100) / 100;
                const risk = (i*7) % 29 < 3;
                return <div key={i} style={{ aspectRatio: "1", background: risk ? "var(--critical)" : `color-mix(in oklch, var(--signal) ${Math.round(v*60+10)}%, var(--ink-3))`, borderRadius: 2 }}/>;
              })}
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>янв · фев · мар · апр</div>
              <div style={{ display: "flex", gap: 10, fontSize: 11 }}>
                <StatDot tone="critical">6 churn</StatDot>
                <StatDot tone="warn">11 риск</StatDot>
                <StatDot tone="ok">231 ок</StatDot>
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

// ── AI Assistant ──────────────────────────────────────────
function PageAI() {
  return (
    <div>
      <TopBar breadcrumbs={["am hub","ai-ассистент"]} title="AI-ассистент"
        subtitle="Чат с данными портфеля · авто-брифы · генерация follow-up"
        actions={<><Btn kind="ghost" size="m">История</Btn><Btn kind="primary" size="m" icon={<I.plus size={14}/>}>Новая сессия</Btn></>}/>
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 280px", gap: 18 }}>
        <Card title="Диалог · 18 апр, 11:42" action={<Badge tone="signal">gpt-5 · data-grounded</Badge>}>
          <div style={{ display: "flex", flexDirection: "column", gap: 14, maxHeight: 540, overflow: "auto" }}>
            <Msg role="user">Какие клиенты в A+ показали худшие результаты за март?</Msg>
            <Msg role="ai">Я нашла 3 клиента в сегменте A+ с отрицательным трендом за март:
              <div style={{ marginTop: 10, background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 4, padding: 10 }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 80px 80px", gap: 10, fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--ink-6)", paddingBottom: 6, borderBottom: "1px solid var(--line-soft)" }}>
                  <span>КЛИЕНТ</span><span>GMV</span><span>Δ</span>
                </div>
                {[["Aura Beauty","₽ 3.1м","−18%"],["СтройМаркет-21","₽ 4.8м","−4%"],["Lumen","₽ 2.6м","−2%"]].map((r,i)=>(
                  <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 80px 80px", gap: 10, fontFamily: "var(--f-mono)", fontSize: 12, padding: "6px 0" }}>
                    <span style={{ color: "var(--ink-8)" }}>{r[0]}</span>
                    <span style={{ color: "var(--ink-7)" }}>{r[1]}</span>
                    <span style={{ color: "var(--critical)" }}>{r[2]}</span>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 8 }}>Основной риск — Aura Beauty. Подготовить план action-итемов?</div>
            </Msg>
            <Msg role="user">Да, подготовь.</Msg>
            <Msg role="ai">План из 5 шагов для Aura Beauty:
              <ol style={{ margin: "8px 0 0", paddingLeft: 18, lineHeight: 1.7 }}>
                <li>Срочная встреча с CMO · сегодня 15:00</li>
                <li>Drilldown воронки оплаты · шаг &laquo;адрес&raquo;</li>
                <li>Предложить персональные условия лояльности</li>
                <li>Вернуть до 10 апр к уровню Q1</li>
                <li>Мониторинг 7 дней · ежедневный чек</li>
              </ol>
            </Msg>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 14, padding: 10, background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 6 }}>
            <input placeholder="Спросите о портфеле, клиенте или задаче…" style={{ flex: 1, background: "transparent", border: 0, color: "var(--ink-8)", outline: "none", fontFamily: "var(--f-display)", fontSize: 13 }}/>
            <Btn size="s" kind="ghost" icon={<I.mic size={12}/>}/>
            <Btn size="s" kind="primary" iconRight={<I.arrow_r size={12}/>}>Отправить</Btn>
          </div>
        </Card>

        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <Card title="Быстрые команды" dense>
            {["Брифинг на завтра","Кто на churn-риске","Собрать QBR для Aura","Перевести встречу","Экспорт в PDF"].map((c,i)=>(
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 0", borderBottom: i===4?"none":"1px solid var(--line-soft)", cursor: "pointer" }}>
                <I.spark size={12} stroke="var(--signal)"/>
                <span style={{ flex: 1, fontSize: 12.5, color: "var(--ink-8)" }}>{c}</span>
                <Kbd>↵</Kbd>
              </div>
            ))}
          </Card>

          <Card title="Контекст сессии" dense>
            <div style={{ fontSize: 12, color: "var(--ink-7)", lineHeight: 1.55 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}><span className="dim">модель</span><span className="mono">gpt-5</span></div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}><span className="dim">документов</span><span className="mono">4 288</span></div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}><span className="dim">свежесть</span><span className="mono">live · 2 мин</span></div>
              <div style={{ display: "flex", justifyContent: "space-between" }}><span className="dim">токенов</span><span className="mono">18.4k</span></div>
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
  return (
    <div>
      <TopBar breadcrumbs={["am hub","мой kpi"]} title="Мой KPI · Q2'26"
        subtitle="Анна Соколова · tier 1 · план до 30 июня"/>
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12 }}>
          {[
            { l: "GMV портфеля", v: "₽ 48м", plan: "₽ 60м", pct: 80, tone: "ok" },
            { l: "Чекапы", v: "32", plan: "40", pct: 80, tone: "ok" },
            { l: "Retention", v: "94%", plan: "92%", pct: 102, tone: "signal" },
            { l: "NPS", v: "62", plan: "70", pct: 88, tone: "warn" },
          ].map((k,i)=>(
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
          <div style={{ display: "grid", gridTemplateColumns: "repeat(13,1fr)", gap: 4, alignItems: "end", height: 160 }}>
            {Array.from({length: 13}).map((_, i) => {
              const h = 30 + Math.abs(Math.sin(i*1.3))*100;
              const active = i <= 3;
              return (
                <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
                  <div style={{ width: "100%", height: h, background: active ? "var(--signal)" : "var(--ink-3)", borderRadius: "2px 2px 0 0" }}/>
                  <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)" }}>W{14+i}</span>
                </div>
              );
            })}
          </div>
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
          {[
            { n: "Брифы клиентов", c: 42, i: "folder" },
            { n: "QBR-презентации", c: 18, i: "doc" },
            { n: "Заметки голосом", c: 37, i: "mic" },
            { n: "Скриншоты", c: 128, i: "eye" },
            { n: "Договоры", c: 14, i: "doc" },
            { n: "Черновики", c: 6, i: "spark" },
          ].map((f,i)=>{
            const Ic = I[f.i];
            return (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 6px", borderBottom: i===5?"none":"1px solid var(--line-soft)", cursor: "pointer" }}>
                <Ic size={14} stroke="var(--ink-6)"/>
                <span style={{ flex: 1, fontSize: 12.5, color: "var(--ink-8)" }}>{f.n}</span>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>{f.c}</span>
              </div>
            );
          })}
        </Card>

        <Card title="Недавние файлы" action={<Btn size="s" kind="ghost" icon={<I.grid size={12}/>}>Grid</Btn>}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 14 }}>
            {[
              { n: "QBR-СтройМаркет-21.pdf",  t: "pdf · 2.1 MB", d: "2 дня назад" },
              { n: "Бриф Aura Beauty.md",     t: "markdown",     d: "3 дня назад" },
              { n: "Заметка · 14:32 · ТехноЛайн.mp3", t: "голос · 3:12", d: "сегодня" },
              { n: "Воронка оплаты.png",      t: "png · 840 KB", d: "вчера" },
              { n: "Шаблон follow-up.docx",   t: "doc",          d: "1 неделя" },
              { n: "KPI-портфель-Q2.xlsx",    t: "spreadsheet",  d: "10 апр" },
            ].map((f,i)=>(
              <div key={i} style={{ background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
                <Placeholder h={90} label={f.t}/>
                <div style={{ padding: 10 }}>
                  <div style={{ fontSize: 12.5, color: "var(--ink-8)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.n}</div>
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", marginTop: 3 }}>{f.d}</div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

// ── Templates ─────────────────────────────────────────────
function PageTemplates() {
  const tpls = [
    { n: "Чекап · стандарт", cat: "чекапы", usage: 164, body: "Добрый день, {{name}}. Коротко синхронизуемся по прогрессу..." },
    { n: "Follow-up после встречи", cat: "email", usage: 98, body: "Спасибо за встречу. По итогам договорились:" },
    { n: "Эскалация · просрочка", cat: "email", usage: 34, body: "Коллеги, по итогам анализа ситуация требует внимания..." },
    { n: "QBR · повестка", cat: "встречи", usage: 76, body: "1. Итоги квартала\n2. KPI\n3. Риски\n4. План Q+1" },
    { n: "Онбординг · шаг 1", cat: "онбординг", usage: 48, body: "Добро пожаловать в команду AM Hub!" },
    { n: "Churn-retention", cat: "email", usage: 12, body: "Мы заметили, что активность снизилась..." },
  ];
  return (
    <div>
      <TopBar breadcrumbs={["am hub","шаблоны"]} title="Шаблоны" subtitle="Follow-up, чекапы, QBR — шаблоны общения с клиентами"
        actions={<Btn kind="primary" size="m" icon={<I.plus size={14}/>}>Новый шаблон</Btn>}/>
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 14 }}>
        {tpls.map((t,i)=>(
          <div key={i} style={{ background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6, padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 500, color: "var(--ink-9)" }}>{t.n}</div>
                <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", marginTop: 3 }}>{t.cat}</div>
              </div>
              <Badge tone="ghost">{t.usage}×</Badge>
            </div>
            <div style={{ fontSize: 12, color: "var(--ink-6)", lineHeight: 1.5, padding: 10, background: "var(--ink-1)", border: "1px solid var(--line-soft)", borderRadius: 4, fontFamily: "var(--f-mono)", fontSize: 11, whiteSpace: "pre-wrap", overflow: "hidden", maxHeight: 70 }}>{t.body}</div>
            <div style={{ display: "flex", gap: 6 }}>
              <Btn size="s" kind="ghost">Превью</Btn>
              <Btn size="s" kind="dim">Применить</Btn>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Auto tasks ────────────────────────────────────────────
function PageAuto() {
  const rules = [
    { on: true,  trig: "GMV −10% за 7 дней",          then: "Создать задачу · приоритет high",  hits: 18 },
    { on: true,  trig: "Чекап просрочен >3 дня",      then: "Уведомить в Telegram + задача",   hits: 42 },
    { on: true,  trig: "Новый клиент попадает в A",   then: "Запланировать welcome-звонок",    hits: 8 },
    { on: false, trig: "Контракт истекает <30 дней",  then: "Создать задачу на обновление",    hits: 0 },
    { on: true,  trig: "Ответа не было 7 дней",       then: "Отправить follow-up шаблон #2",   hits: 67 },
  ];
  return (
    <div>
      <TopBar breadcrumbs={["am hub","автозадачи"]} title="Автозадачи"
        subtitle="Правила `IF-THEN`: когда система создаёт задачи автоматически"
        actions={<Btn kind="primary" size="m" icon={<I.plus size={14}/>}>Новое правило</Btn>}/>
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12 }}>
          <KPI label="Правил активно" value="4" unit="/ 5"/>
          <KPI label="Создано задач · 30д" value="135" tone="signal" delta="+22%"/>
          <KPI label="Ср. время реакции" value="44" unit="минут" tone="ok" delta="−28%"/>
        </div>

        <Card title="Правила">
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
  const cols = [
    { t: "Q1 · готово",      tone: "ok",       items: ["Единая карточка клиента", "Интеграция KTalk", "Автосинхр. Merchrules"] },
    { t: "Q2 · в работе",    tone: "signal",   items: ["AI-ассистент с контекстом", "QBR-календарь", "Мобильная версия"] },
    { t: "Q3 · план",        tone: "info",     items: ["Voice-заметки → follow-up", "Голосовой ассистент", "Интеграция Diginetica"] },
    { t: "Бэклог",           tone: "neutral",  items: ["Расширенная аналитика", "Темплейты на SQL", "API для клиентов", "Автоотчёты"] },
  ];
  return (
    <div>
      <TopBar breadcrumbs={["am hub","роадмап"]} title="Роадмап"
        subtitle="Что команда строит в AM Hub · 2026"/>
      <div style={{ padding: "22px 28px 40px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14 }}>
          {cols.map((c,i)=>(
            <div key={i} style={{ background: "var(--ink-2)", border: "1px solid var(--line)", borderTop: `3px solid var(--${c.tone})`, borderRadius: "0 0 6px 6px", padding: 14 }}>
              <div className="mono" style={{ fontSize: 11, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 12 }}>{c.t}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {c.items.map((it,j)=>(
                  <div key={j} style={{ padding: 10, background: "var(--ink-1)", border: "1px solid var(--line-soft)", borderRadius: 4 }}>
                    <div style={{ fontSize: 12.5, color: "var(--ink-8)" }}>{it}</div>
                  </div>
                ))}
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
          {[
            { t: "Пересмотр сегментации A/B", owner: "Анна С.", due: "22 апр", pr: "high" },
            { t: "Обновление шаблонов чекапа", owner: "Лиза М.", due: "25 апр", pr: "med" },
            { t: "Документация API для Merchrules", owner: "Кирилл В.", due: "1 мая", pr: "low" },
            { t: "Тренинг по новым правилам Diginetica", owner: "все", due: "3 мая", pr: "med" },
            { t: "Retro Q1", owner: "Павел Р.", due: "30 апр", pr: "low" },
          ].map((r,i,a)=>(
            <div key={i} style={{ display: "grid", gridTemplateColumns: "20px 1fr 180px 80px 80px", gap: 14, padding: "12px 6px", borderBottom: i===a.length-1?"none":"1px solid var(--line-soft)", alignItems: "center" }}>
              <input type="checkbox" style={{ accentColor: "var(--signal)" }}/>
              <span style={{ fontSize: 13, color: "var(--ink-8)" }}>{r.t}</span>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}><Avatar name={r.owner} size={20}/><span style={{ fontSize: 12, color: "var(--ink-7)" }}>{r.owner}</span></div>
              <span className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>{r.due}</span>
              <Badge tone={r.pr==="high"?"warn":r.pr==="med"?"info":"neutral"} dot>{r.pr}</Badge>
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
}

// ── Extension install page ────────────────────────────────
function PageExtInstall() {
  return (
    <div>
      <TopBar breadcrumbs={["am hub","расширение"]} title="Расширение браузера"
        subtitle="Синхронизация Merchrules → AM Hub в один клик"/>
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 420px", gap: 28 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <Card title="Установка · 3 шага">
            {[
              { s: "01", t: "Скачать архив", sub: "amhub-sync.zip · 42 KB", a: "Скачать" },
              { s: "02", t: "Открыть chrome://extensions и включить режим разработчика", sub: "в правом верхнем углу" },
              { s: "03", t: "Load unpacked → выбрать распакованную папку", sub: "расширение появится в тулбаре" },
            ].map((step,i)=>(
              <div key={i} style={{ display: "grid", gridTemplateColumns: "48px 1fr 100px", gap: 14, padding: "16px 0", borderBottom: i===2?"none":"1px solid var(--line-soft)", alignItems: "center" }}>
                <span className="mono" style={{ fontSize: 22, color: "var(--signal)", fontWeight: 500 }}>{step.s}</span>
                <div>
                  <div style={{ fontSize: 14, color: "var(--ink-9)", fontWeight: 500 }}>{step.t}</div>
                  <div className="mono" style={{ fontSize: 11, color: "var(--ink-5)", marginTop: 3 }}>{step.sub}</div>
                </div>
                {step.a && <Btn size="s" kind="primary" icon={<I.download size={12}/>}>{step.a}</Btn>}
              </div>
            ))}
          </Card>

          <Card title="Что делает расширение">
            {[
              { i: "refresh", t: "Синхронизирует клиентов и задачи каждые 15 минут" },
              { i: "bell",    t: "Уведомляет при критических изменениях в Merchrules" },
              { i: "lock",    t: "Хранит ключи локально, не отправляет на сервер" },
              { i: "spark",   t: "Автоматически создаёт задачи по правилам" },
            ].map((r,i)=>{const Ic = I[r.i]; return (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 0", borderBottom: i===3?"none":"1px solid var(--line-soft)" }}>
                <div style={{ width: 28, height: 28, borderRadius: 4, background: "var(--ink-1)", border: "1px solid var(--line)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--signal)" }}><Ic size={14}/></div>
                <span style={{ fontSize: 13, color: "var(--ink-8)" }}>{r.t}</span>
              </div>
            );})}
          </Card>
        </div>

        <div>
          <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 10 }}>превью popup</div>
          <ExtensionPopup state="connected"/>
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
