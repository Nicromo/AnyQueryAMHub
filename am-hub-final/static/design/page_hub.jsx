// page_hub.jsx — Command Center

// Форматёр цифр: 1234 → "1 234"
function _fmt(n) {
  if (n == null || isNaN(n)) return "—";
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

// Парсер GMV-строки "₽ 4.8м" → 4_800_000 (приблизительно).
// Сервер форматирует mrr в строку, raw-число у нас нет — пытаемся восстановить.
function _parseGmv(gmv) {
  if (!gmv || typeof gmv !== "string") return 0;
  const num = parseFloat(gmv.replace(/[^\d.,]/g, "").replace(",", "."));
  if (isNaN(num)) return 0;
  if (gmv.includes("м")) return num * 1_000_000;
  if (gmv.includes("к")) return num * 1_000;
  return num;
}

function _formatGmv(rub) {
  if (!rub) return "₽ 0";
  if (rub >= 1_000_000) {
    const v = rub / 1_000_000;
    return "₽ " + (v >= 10 ? v.toFixed(0) : v.toFixed(1).replace(/\.0$/, "")) + "м";
  }
  if (rub >= 1_000) return "₽ " + Math.round(rub / 1000) + "к";
  return "₽ " + Math.round(rub);
}

// Агрегат MRR по сегменту. Возвращает { label → сумма }.
function _aggregateMrr(clients) {
  const buckets = { "ENT/SME+": 0, "SME": 0, "SMB": 0, "SS": 0, "NEW": 0 };
  (clients || []).forEach((c) => {
    const m = Number(c.gmv && c.gmv.replace ? c.gmv.replace(/[^\d.]/g, "") : 0);
    // gmv у нас строка "₽ 5.8м" — парсим ниже из raw-поля mrr, пока просто по сегменту
    const seg = (c.seg || "").toUpperCase();
    const bucket =
      seg === "ENT" || seg === "SME+" ? "ENT/SME+" :
      seg === "SME" || seg === "SME-" ? "SME" :
      seg === "SMB" ? "SMB" :
      seg === "SS" ? "SS" : "NEW";
    buckets[bucket] = (buckets[bucket] || 0) + (m || 0);
  });
  return buckets;
}

function PageHub() {
  const S  = (typeof window !== "undefined" && window.__SIDEBAR_STATS)  || {};
  const CL = (typeof window !== "undefined" && window.CLIENTS)          || [];
  const TK = (typeof window !== "undefined" && window.TASKS)            || [];
  const MT = (typeof window !== "undefined" && window.MEETINGS)         || [];

  // Агрегат GMV по бакетам сегментов (A+/ENT → signal, SME → info, SMB → warn, SS/NEW → ok)
  const buckets = [
    { key: "ENT+SME+", label: "ENT / SME+", segs: ["ENT", "SME+"], color: "signal", rub: 0 },
    { key: "SME",      label: "SME",        segs: ["SME", "SME-"], color: "info",   rub: 0 },
    { key: "SMB",      label: "SMB",        segs: ["SMB"],         color: "warn",   rub: 0 },
    { key: "SS",       label: "SS",         segs: ["SS"],          color: "ok",     rub: 0 },
  ];
  let totalGmv = 0;
  CL.forEach((c) => {
    const rub = _parseGmv(c.gmv);
    totalGmv += rub;
    const b = buckets.find((x) => x.segs.includes((c.seg || "").toUpperCase()));
    if (b) b.rub += rub;
  });
  const pctOf = (rub) => (totalGmv > 0 ? Math.round((rub / totalGmv) * 100) : 0);

  // Фокус на сегодня
  const meetingsToday = MT.filter((m) => m.day === "сегодня").length;
  const overdueTasks  = TK.filter((t) => typeof t.due === "string" && t.due.indexOf("просроч") !== -1).length;
  const firstRisk = CL.find((c) => c.status === "risk");

  // Сигналы — реальные клиенты в статусе risk/warn
  const signals = CL
    .filter((c) => c.status === "risk" || c.status === "warn")
    .slice(0, 4)
    .map((c) => ({
      id: c.id,
      tone: c.status === "risk" ? "critical" : "warn",
      title: c.name,
      note:
        c.status === "risk"
          ? `GMV ${c.delta || "—"}, ${c.days_since != null ? `чекап ${c.days_since} дн. назад` : c.stage}`
          : `${c.stage || "проверить"} · ${c.next || "—"}`,
      icon: c.status === "risk" ? "flame" : "eye",
      meta: `${c.seg || "—"} · ${c.pm || "—"}`,
    }));

  return (
    <div>
      <TopBar
        breadcrumbs={["am hub", "командный центр"]}
        title="Командный центр"
        subtitle="Единое управление портфелем, задачами и инструментами команды"
        meta={
          <>
            <div style={{ display: "flex", gap: 16, paddingRight: 18, borderRight: "1px solid var(--line)" }}>
              <div>
                <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>неделя</div>
                <div className="mono" style={{ fontSize: 14, color: "var(--ink-8)", fontWeight: 500 }}>{(function(){
                  // ISO week: Mon–Sun, week starts Monday
                  const d = new Date();
                  const day = d.getDay(); // 0=Sun, 1=Mon … 6=Sat
                  const mon = new Date(d); mon.setDate(d.getDate() - (day === 0 ? 6 : day - 1));
                  const sun = new Date(mon); sun.setDate(mon.getDate() + 6);
                  // ISO week number (Thu-anchor method)
                  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
                  t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
                  const y0 = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
                  const wn = Math.ceil(((t - y0) / 86400000 + 1) / 7);
                  const mo = ["янв","фев","мар","апр","май","июн","июл","авг","сен","окт","ноя","дек"];
                  const monS = mon.getDate() + (mon.getMonth() !== sun.getMonth() ? " " + mo[mon.getMonth()] : "");
                  const sunS = sun.getDate() + " " + mo[sun.getMonth()];
                  return `W${wn} · ${monS}–${sunS}`;
                })()}</div>
              </div>
              <div>
                <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>статус</div>
                <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 6, height: 6, borderRadius: 999, background: "var(--ok)", boxShadow: "0 0 8px var(--ok)" }}/>
                  <span className="mono" style={{ fontSize: 13, color: "var(--ok)", fontWeight: 500 }}>all systems</span>
                </div>
              </div>
            </div>
          </>
        }
        actions={
          <>
            <Btn kind="ghost" size="m" icon={<I.refresh size={14}/>}
              onClick={async () => {
                if (!await appConfirm("Синхронизировать из Airtable + Merchrules?")) return;
                appToast("⏳ Синк запущен…", { tone: "info", duration: 6000 });
                let atRes = {}, mrRes = {};
                // Airtable
                try {
                  const r1 = await fetch("/api/sync/airtable", {
                    method: "POST", credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({}),
                  });
                  const txt1 = await r1.text();
                  try { atRes = JSON.parse(txt1); } catch (_) { atRes = { error: `HTTP ${r1.status}: ${txt1.slice(0, 200)}` }; }
                  if (!r1.ok && !atRes.error) atRes.error = `HTTP ${r1.status}`;
                } catch (e) { atRes = { error: "fetch: " + e.message }; }
                // Merchrules (необязательный — если не настроено, пропустим без крика)
                try {
                  const r2 = await fetch("/api/sync/merchrules", {
                    method: "POST", credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({}),
                  });
                  const txt2 = await r2.text();
                  try { mrRes = JSON.parse(txt2); } catch (_) { mrRes = { error: `HTTP ${r2.status}: ${txt2.slice(0, 200)}` }; }
                  if (!r2.ok && !mrRes.error) mrRes.error = `HTTP ${r2.status}`;
                } catch (e) { mrRes = { error: "fetch: " + e.message }; }

                const atLine = atRes.error
                  ? `❌ Airtable: ${atRes.error}`
                  : `✅ Airtable: создано ${atRes.created || 0}, обновлено ${atRes.updated || 0}, пропущено ${atRes.skipped || 0}${atRes.payment_updated != null ? `, оплата ${atRes.payment_updated}` : ""} (всего в ответе: ${atRes.total ?? "?"})`;
                const mrLine = mrRes.error
                  ? `❌ Merchrules: ${mrRes.error}`
                  : `✅ Merchrules: клиентов ${mrRes.clients_synced || mrRes.synced || 0}, задач ${mrRes.tasks_synced || 0}`;
                const atSynced = (atRes.created || 0) + (atRes.updated || 0);
                const mrSynced = mrRes.clients_synced || mrRes.synced || 0;
                const hasError = atRes.error || mrRes.error;
                const tone = hasError ? "error" : (atSynced + mrSynced > 0 ? "ok" : "warn");
                appToast(`${atLine}\n${mrLine}`, { tone, duration: 12000 });
                // Reload только если хоть что-то реально засинкалось, иначе оставляем тост.
                if (atSynced + mrSynced > 0) setTimeout(() => location.reload(), 2500);
              }}
            >Синхронизировать</Btn>
          </>
        }
      />

      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 22 }}>

        {/* ── KPI row ── реальные цифры из __SIDEBAR_STATS ──── */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12 }}>
          <KPI label="Клиентов в портфеле" value={_fmt(S.clientsTotal)} sub="в вашем скоупе" />
          <KPI label="Просрочено чекапов" value={_fmt(S.overdue)} tone={S.overdue > 0 ? "critical" : undefined} sub={S.overdue > 0 ? "требуют действий" : "всё под контролем"} />
          <KPI label="Скоро чекап" value={_fmt(S.dueCheckup)} tone={S.dueCheckup > 0 ? "warn" : undefined} sub="в ближайшие 7 дней" />
          <KPI label="Открытых задач" value={_fmt(S.tasksActive)} sub="plan + in_progress + blocked" />
          <KPI label="Входящих" value={_fmt(S.inbox || 0)} sub="непрочитанных" />
        </div>

        {/* ── Main grid ────────────────────────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 18 }}>

          {/* LEFT COLUMN */}
          <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0 }}>

            {/* signals board — клиенты со статусом risk/warn */}
            <Card title="Сигналы — требуют внимания" action={
              <span className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>
                {signals.length} {signals.length === 1 ? "сигнал" : "сигналов"}
              </span>
            }>
              {signals.length === 0 ? (
                <div style={{ padding: "20px 0", color: "var(--ink-6)", fontSize: 13, textAlign: "center" }}>
                  Сейчас нет клиентов в зоне риска — всё под контролем.
                </div>
              ) : (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                {signals.map((s, i) => {
                  const Ic = I[s.icon] || I.alert;
                  return (
                    <div key={i}
                      onClick={() => { if (s.id) window.location.href = "/design/client/" + s.id; }}
                      style={{
                      padding: 14,
                      background: "var(--ink-1)",
                      border: "1px solid var(--line)",
                      borderLeft: `2px solid ${s.tone === "critical" ? "var(--critical)" : "var(--warn)"}`,
                      borderRadius: 4,
                      display: "flex", gap: 12, cursor: "pointer",
                    }}>
                      <div style={{
                        width: 30, height: 30, borderRadius: 4,
                        background: s.tone === "critical" ? "color-mix(in oklch, var(--critical) 14%, transparent)" : "color-mix(in oklch, var(--warn) 14%, transparent)",
                        color: s.tone === "critical" ? "var(--critical)" : "var(--warn)",
                        display: "flex", alignItems: "center", justifyContent: "center",
                        flexShrink: 0,
                      }}>
                        <Ic size={16}/>
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                          <div style={{ fontSize: 13.5, fontWeight: 500, color: "var(--ink-9)" }}>{s.title}</div>
                          <span className="mono" style={{ fontSize: 10, color: "var(--ink-5)" }}>· {s.meta}</span>
                        </div>
                        <div style={{ fontSize: 12, color: "var(--ink-6)", lineHeight: 1.4 }}>{s.note}</div>
                      </div>
                      <I.arrow_r size={14} stroke="var(--ink-5)" style={{ flexShrink: 0, marginTop: 4 }}/>
                    </div>
                  );
                })}
              </div>
              )}
            </Card>

            {/* portfolio pulse — NRR */}
            <NrrPulse fallbackTotalGmv={totalGmv} fallbackClientsCount={CL.length}/>

            {/* attention + tickets — заменили инструменты/cron на более полезные блоки */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
              <AttentionList clients={CL}/>
              <FreshTickets/>
            </div>

          </div>

          {/* RIGHT COLUMN */}
          <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0 }}>

            {/* today focus */}
            <Card title="Фокус на сегодня" action={<a className="mono" style={{ fontSize: 11, color: "var(--signal)" }}>план дня →</a>}>
              <div style={{
                padding: 12,
                background: "var(--ink-0)",
                border: "1px solid var(--line)",
                borderRadius: 4,
                marginBottom: 12,
              }}>
                <div style={{ fontSize: 13, color: "var(--ink-7)", lineHeight: 1.5 }}>
                  {meetingsToday === 0 && overdueTasks === 0 && !firstRisk
                    ? "Сегодня ничего не горит — используйте время на план развития аккаунтов."
                    : (
                      <>
                        Сегодня в приоритете{" "}
                        <span style={{ color: "var(--signal)" }}>{meetingsToday} {meetingsToday === 1 ? "встреча" : (meetingsToday < 5 && meetingsToday > 0 ? "встречи" : "встреч")}</span>
                        {overdueTasks > 0 && (
                          <>
                            {" "}и{" "}
                            <span style={{ color: "var(--critical)" }}>{overdueTasks} {overdueTasks === 1 ? "просроченная задача" : (overdueTasks < 5 ? "просроченных задачи" : "просроченных задач")}</span>
                          </>
                        )}
                        {firstRisk && (
                          <>. AI рекомендует сначала закрыть риск по <span style={{ color: "var(--ink-9)", fontWeight: 500 }}>{firstRisk.name}</span></>
                        )}
                        .
                      </>
                    )}
                </div>
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {MEETINGS.filter(m => m.day === "сегодня" || m.day === "завтра").map((m, i) => (
                  <div key={i} style={{
                    display: "flex", alignItems: "center", gap: 12,
                    padding: "10px 12px",
                    background: "var(--ink-1)",
                    borderRadius: 4,
                    borderLeft: `2px solid ${m.mood === "risk" ? "var(--critical)" : m.mood === "warn" ? "var(--warn)" : "var(--ok)"}`,
                  }}>
                    <div style={{ width: 54, flexShrink: 0 }}>
                      <div className="mono" style={{ fontSize: 13, color: "var(--ink-9)", fontWeight: 500 }}>{m.when}</div>
                      <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{m.day}</div>
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12.5, fontWeight: 500, color: "var(--ink-8)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.client}</div>
                      <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{m.type}</div>
                    </div>
                    <Seg value={m.seg}/>
                  </div>
                ))}
              </div>

              <Btn kind="ghost" size="s" full style={{ marginTop: 10 }} iconRight={<I.arrow_r size={12}/>}>
                Все встречи на неделю
              </Btn>
            </Card>

            {/* urgent tasks */}
            <Card title="Срочные задачи" dense>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {TASKS.slice(0, 5).map((t, i) => (
                  <div key={i} style={{
                    display: "flex", alignItems: "flex-start", gap: 10,
                    padding: "10px 4px",
                    borderBottom: i === 4 ? "none" : "1px solid var(--line-soft)",
                  }}>
                    <input type="checkbox" style={{
                      accentColor: "var(--signal)", marginTop: 2, flexShrink: 0,
                    }}/>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12.5, color: "var(--ink-8)", lineHeight: 1.35 }}>{t.title}</div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
                        <span className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{t.client}</span>
                        <span style={{ color: "var(--ink-4)" }}>·</span>
                        <span className="mono" style={{
                          fontSize: 10,
                          color: t.priority === "critical" ? "var(--critical)" :
                                 t.priority === "high" ? "var(--warn)" : "var(--ink-6)",
                          textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500,
                        }}>{t.due}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </Card>

            {/* team activity */}
            <Card title="Лента команды" dense>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {ACTIVITY.map((a, i) => (
                  <div key={i} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                    <Avatar name={a.who} size={24} tone={a.mood === "info" ? "neutral" : "signal"}/>
                    <div style={{ flex: 1, minWidth: 0, fontSize: 12, color: "var(--ink-7)", lineHeight: 1.4 }}>
                      <span style={{ color: "var(--ink-9)", fontWeight: 500 }}>{a.who}</span>
                      {" "}{a.what}{" "}
                      <span style={{ color: "var(--ink-8)", fontWeight: 500 }}>{a.obj}</span>
                    </div>
                    <span className="mono" style={{ fontSize: 10, color: "var(--ink-5)", flexShrink: 0 }}>{a.when}</span>
                  </div>
                ))}
              </div>
            </Card>

            {/* weekly snapshot — заполняет пустое пространство под лентой */}
            <WeeklySnapshot clients={CL} tasks={TK} meetings={MT}/>

          </div>
        </div>
      </div>
    </div>
  );
}

// ── Big sparkline/area chart ──────────────────────────────
function BigSpark() {
  let data = (typeof window !== "undefined" && window.GMV_SPARK) || [];
  const w = 520, h = 120;
  // Фолбек: если история пустая, но клиенты с MRR есть — рисуем «плоскую»
  // линию на текущем уровне, чтобы виджет не пустовал.
  if (!data.length) {
    const CL = (typeof window !== "undefined" && window.CLIENTS) || [];
    const totalNow = CL.reduce((s, c) => {
      const v = c && typeof c.gmv === "string" ? c.gmv : "";
      const num = parseFloat(v.replace(/[^\d.,]/g, "").replace(",", "."));
      if (isNaN(num)) return s;
      return s + (v.includes("м") ? num * 1e6 : v.includes("к") ? num * 1e3 : num);
    }, 0);
    if (totalNow > 0) data = Array(6).fill(Math.round(totalNow));
  }
  if (!data.length) {
    return (
      <div style={{ width: "100%", height: h, display: "flex", alignItems: "center", justifyContent: "center",
                    color: "var(--ink-6)", fontSize: 12.5, fontFamily: "var(--f-mono)",
                    background: "var(--ink-1)", border: "1px dashed var(--line)", borderRadius: 4 }}>
        Данных GMV пока нет — появятся после первой синхронизации
      </div>
    );
  }
  const min = Math.min(...data), max = Math.max(...data), rng = Math.max(1, max - min);
  const pts = data.map((v, i) => [(i / Math.max(1, data.length-1)) * w, h - ((v-min)/rng) * (h-8) - 4]);
  const path = pts.map((p,i) => (i?"L":"M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = path + ` L ${w} ${h} L 0 ${h} Z`;
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      <defs>
        <linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor="var(--signal)" stopOpacity="0.35"/>
          <stop offset="100%" stopColor="var(--signal)" stopOpacity="0"/>
        </linearGradient>
      </defs>
      {[0.25, 0.5, 0.75].map(p => (
        <line key={p} x1="0" x2={w} y1={h*p} y2={h*p} stroke="var(--line-soft)" strokeDasharray="2 4"/>
      ))}
      <path d={area}  fill="url(#grad)" />
      <path d={path}  fill="none" stroke="var(--signal)" strokeWidth="1.5"/>
      <circle cx={pts[pts.length-1][0]} cy={pts[pts.length-1][1]} r="3" fill="var(--signal)"/>
      <circle cx={pts[pts.length-1][0]} cy={pts[pts.length-1][1]} r="7" fill="var(--signal)" fillOpacity="0.18"/>
    </svg>
  );
}

// ── NRR Pulse — пульс портфеля через Net Revenue Retention ──
function NrrPulse({ fallbackTotalGmv = 0, fallbackClientsCount = 0 }) {
  const [period, setPeriod] = React.useState("30d");
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true); setErr(null);
    (async () => {
      try {
        const r = await fetch(`/api/me/nrr-pulse?period=${period}`, { credentials: "include" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const d = await r.json();
        if (!cancelled) { setData(d); setLoading(false); }
      } catch (e) {
        if (!cancelled) { setErr(e.message); setLoading(false); }
      }
    })();
    return () => { cancelled = true; };
  }, [period]);

  const nrrTotal = data && data.nrr_total != null ? Number(data.nrr_total) : null;
  const gmvTotal = data && data.gmv_total != null ? Number(data.gmv_total) : fallbackTotalGmv;
  const clientsCnt = data && data.clients_count != null ? Number(data.clients_count) : fallbackClientsCount;
  const bySeg = (data && data.by_segment) || {};

  const nrrColor = (v) => {
    if (v == null) return "var(--ink-5)";
    if (v >= 100) return "var(--ok)";
    if (v >= 90) return "var(--warn)";
    return "var(--critical)";
  };
  const nrrTone = (v) => {
    if (v == null) return "signal";
    if (v >= 100) return "ok";
    if (v >= 90) return "warn";
    return "critical";
  };
  const fmtNrr = (v) => v == null ? "—" : `${v.toFixed(1)}%`;

  const segOrder = ["ENT", "SME+", "SME", "SMB", "SS"];
  const periods = [
    { k: "7d",      l: "7д"      },
    { k: "30d",     l: "30д"     },
    { k: "quarter", l: "квартал" },
  ];

  return React.createElement(Card, {
    title: "Пульс портфеля · NRR",
    action: React.createElement("div", { style: { display: "flex", gap: 6 } },
      periods.map((p) => React.createElement(Btn, {
        key: p.k, size: "s", kind: period === p.k ? "dim" : "ghost",
        onClick: () => setPeriod(p.k),
      }, p.l)),
    ),
  },
    React.createElement("div", { style: { display: "grid", gridTemplateColumns: "1fr 220px", gap: 20, alignItems: "stretch" } },
      React.createElement("div", null,
        React.createElement("div", { style: { display: "flex", alignItems: "baseline", gap: 12, marginBottom: 6 } },
          React.createElement("div", {
            style: {
              fontSize: 48, fontWeight: 500, letterSpacing: "-0.03em", lineHeight: 1,
              color: nrrColor(nrrTotal),
              fontVariantNumeric: "tabular-nums",
            },
          }, loading ? "…" : (err ? "—" : fmtNrr(nrrTotal))),
          React.createElement("div", { className: "mono", style: { fontSize: 12, color: "var(--ink-5)", fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.08em" } },
            "NRR"),
        ),
        React.createElement("div", { className: "mono", style: { fontSize: 11.5, color: "var(--ink-6)", marginBottom: 14 } },
          `оборот ${_formatGmv(gmvTotal)} / ${clientsCnt} ${clientsCnt === 1 ? "клиент" : (clientsCnt < 5 && clientsCnt > 0 ? "клиента" : "клиентов")}`),
        err && React.createElement("div", { style: { fontSize: 11.5, color: "var(--critical)", marginBottom: 10 } },
          "Не удалось загрузить NRR: " + err),
        React.createElement(BigSpark, null),
      ),
      React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 10, borderLeft: "1px solid var(--line)", paddingLeft: 18 } },
        React.createElement("div", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 2 } },
          "NRR по сегментам"),
        segOrder.map((seg) => {
          const v = bySeg[seg];
          const isNum = typeof v === "number" && !isNaN(v);
          return React.createElement("div", { key: seg },
            React.createElement("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 } },
              React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.08em" } }, seg),
              React.createElement("span", { style: { fontSize: 12, fontWeight: 500, color: isNum ? nrrColor(v) : "var(--ink-5)", fontVariantNumeric: "tabular-nums" } },
                isNum ? fmtNrr(v) : "—"),
            ),
            React.createElement(Progress, {
              value: isNum ? Math.max(0, Math.min(120, v)) : 0,
              max: 120, tone: nrrTone(v), h: 3,
            }),
          );
        }),
      ),
    ),
  );
}
window.NrrPulse = NrrPulse;

window.PageHub = PageHub;


// ── Replacement blocks for old ИНСТРУМЕНТЫ / АВТОМАТИЗАЦИЯ ───────────

// Клиенты, требующие внимания: churn-риск + просрочка чекапа + давний контакт.
function AttentionList({ clients }) {
  const CL = Array.isArray(clients) ? clients : [];
  // Сортируем: сначала risk, потом warn, потом по last contact (давно → раньше)
  const ranked = CL.filter(c => {
    if (c.status === "risk" || c.status === "warn") return true;
    if (typeof c.days_since === "number" && c.days_since >= 30) return true;
    if (c.health_score != null && c.health_score < 0.4) return true;
    return false;
  }).sort((a, b) => {
    const rank = s => s === "risk" ? 0 : s === "warn" ? 1 : 2;
    if (rank(a.status) !== rank(b.status)) return rank(a.status) - rank(b.status);
    return (b.days_since || 0) - (a.days_since || 0);
  }).slice(0, 5);

  return React.createElement(Card, {
    title: "Требуют внимания",
    action: React.createElement("a", {
      className: "mono", href: "/design/clients",
      style: { fontSize: 11, color: "var(--ink-6)", textDecoration: "none" },
    }, `${ranked.length} из ${CL.length}`),
  },
    ranked.length === 0
      ? React.createElement("div", { style: { fontSize: 12.5, color: "var(--ink-6)", padding: "14px 0" } },
          "Сейчас все клиенты в норме.")
      : React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 6 } },
          ranked.map((c) => {
            const tone = c.status === "risk" ? "critical" : c.status === "warn" ? "warn" : "ink-4";
            const toneVar = tone === "critical" ? "var(--critical)" : tone === "warn" ? "var(--warn)" : "var(--ink-4)";
            const reason = c.status === "risk"
              ? (c.delta && c.delta !== "—" ? `GMV ${c.delta}` : "churn-риск")
              : (c.days_since != null ? `контакт ${c.days_since} дн назад` : (c.stage || "требует проверки"));
            return React.createElement("a", {
              key: c.id,
              href: `/design/client/${c.id}`,
              style: {
                display: "grid",
                gridTemplateColumns: "6px 1fr auto",
                gap: 10,
                padding: "10px 12px",
                background: "var(--ink-1)",
                borderRadius: 4,
                textDecoration: "none",
                color: "inherit",
                alignItems: "center",
              },
            },
              React.createElement("span", { style: {
                width: 3, height: 28, borderRadius: 2, background: toneVar,
              } }),
              React.createElement("div", { style: { minWidth: 0 } },
                React.createElement("div", { style: {
                  fontSize: 13, fontWeight: 500, color: "var(--ink-8)",
                  whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                } }, c.name),
                React.createElement("div", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-5)" } },
                  `${c.seg || "—"} · ${reason}`),
              ),
              React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: toneVar } },
                c.status === "risk" ? "risk" : c.status === "warn" ? "warn" : "old"),
            );
          })
        )
  );
}
window.AttentionList = AttentionList;


// Свежие тикеты из Time (Mattermost). Показывает последние 5 открытых.
function FreshTickets() {
  const [tickets, setTickets] = React.useState(null);
  const [err, setErr] = React.useState(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Нет эндпоинта списка по всем — агрегируем через список клиентов
        const CL = (typeof window !== "undefined" && window.CLIENTS) || [];
        const ids = CL.slice(0, 50).map(c => c.id).filter(Boolean);
        const results = [];
        for (const id of ids) {
          try {
            const r = await fetch(`/api/clients/${id}/tickets?status=open,in_progress&limit=3`, { credentials: "include" });
            if (!r.ok) continue;
            const d = await r.json();
            (d.tickets || []).forEach(t => results.push({ ...t, _client_id: id, _client_name: (CL.find(x => x.id === id) || {}).name || "—" }));
          } catch (_) {}
          if (results.length >= 10) break;
        }
        // Сортируем по opened_at desc, топ-5
        results.sort((a, b) => (new Date(b.opened_at || 0)) - (new Date(a.opened_at || 0)));
        if (!cancelled) setTickets(results.slice(0, 5));
      } catch (e) { if (!cancelled) setErr(e.message); }
    })();
    return () => { cancelled = true; };
  }, []);

  const body = (() => {
    if (err) return React.createElement("div", { style: { color: "var(--critical)", fontSize: 12.5, padding: "14px 0" } }, "Ошибка: " + err);
    if (tickets === null) return React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "14px 0" } }, "Загрузка…");
    if (!tickets.length) return React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "14px 0" } },
      "Открытых тикетов нет. Синк Tbank Time через расширение.");
    return React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 6 } },
      tickets.map(t => {
        const statusColor = t.status === "open" ? "var(--critical)" : "var(--warn)";
        return React.createElement("a", {
          key: t.id,
          href: `/design/client/${t._client_id}#tickets`,
          style: {
            display: "grid",
            gridTemplateColumns: "6px 1fr auto",
            gap: 10,
            padding: "10px 12px",
            background: "var(--ink-1)",
            borderRadius: 4,
            textDecoration: "none",
            color: "inherit",
            alignItems: "center",
          },
        },
          React.createElement("span", { style: { width: 3, height: 28, borderRadius: 2, background: statusColor } }),
          React.createElement("div", { style: { minWidth: 0 } },
            React.createElement("div", { style: {
              fontSize: 13, fontWeight: 500, color: "var(--ink-8)",
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            } }, t.title || "Без темы"),
            React.createElement("div", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-5)" } },
              `${t._client_name} · ${t.comments_count || 0} комм.`),
          ),
          React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: statusColor } },
            t.status === "open" ? "open" : "work"),
        );
      })
    );
  })();

  return React.createElement(Card, {
    title: "Свежие тикеты",
    action: React.createElement("button", {
      className: "mono",
      onClick: async () => {
        try {
          const r = await fetch("/api/tickets/sync", { method: "POST", credentials: "include" });
          const d = await r.json().catch(() => ({}));
          appToast(d.ok ? `Синк: новых ${d.ingested||0}, обновлено ${d.updated||0}` : (d.error || "Ошибка"), d.ok ? "ok" : "error");
          location.reload();
        } catch (e) { appToast("Ошибка: " + e.message, "error"); }
      },
      style: {
        background: "none", border: 0, fontSize: 11, color: "var(--ink-6)", cursor: "pointer",
      },
    }, "↻ синк"),
  }, body);
}
window.FreshTickets = FreshTickets;


// Еженедельный снимок — сегментация портфеля + что на этой неделе
function WeeklySnapshot({ clients, tasks, meetings }) {
  const CL = Array.isArray(clients) ? clients : [];
  const TK = Array.isArray(tasks) ? tasks : [];
  const MT = Array.isArray(meetings) ? meetings : [];

  const total = CL.length;
  const risks = CL.filter(c => c.status === "risk").length;
  const warns = CL.filter(c => c.status === "warn").length;
  const ok    = CL.filter(c => c.status !== "risk" && c.status !== "warn").length;

  const openTasks = TK.filter(t => typeof t.due === "string" && t.due.indexOf("просроч") === -1).length;
  const overdue   = TK.filter(t => typeof t.due === "string" && t.due.indexOf("просроч") !== -1).length;
  const meetingsWk = MT.length;

  const Row = ({ label, value, tone }) => React.createElement("div", {
    style: {
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "8px 0",
      borderBottom: "1px solid var(--line-soft)",
    }
  },
    React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.08em" } }, label),
    React.createElement("span", { className: "mono", style: { fontSize: 13, fontWeight: 500, color: tone || "var(--ink-9)" } }, value),
  );

  return React.createElement(Card, { title: "Снимок недели", dense: true },
    React.createElement("div", null,
      React.createElement(Row, { label: "клиентов",     value: String(total) }),
      React.createElement(Row, { label: "в риске",      value: String(risks), tone: risks ? "var(--critical)" : "var(--ink-9)" }),
      React.createElement(Row, { label: "требуют внимания", value: String(warns), tone: warns ? "var(--warn)" : "var(--ink-9)" }),
      React.createElement(Row, { label: "в норме",      value: String(ok), tone: "var(--ok)" }),
      React.createElement(Row, { label: "встреч на неделе", value: String(meetingsWk) }),
      React.createElement(Row, { label: "задач открыто",    value: String(openTasks) }),
      React.createElement(Row, { label: "задач просрочено", value: String(overdue), tone: overdue ? "var(--critical)" : "var(--ink-9)" }),
    )
  );
}
window.WeeklySnapshot = WeeklySnapshot;
