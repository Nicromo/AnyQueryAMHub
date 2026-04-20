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
                if (!window.confirm("Синхронизировать из Airtable + Merchrules?")) return;
                let atRes = {}, mrRes = {};
                try {
                  const r1 = await fetch("/api/sync/airtable", {
                    method: "POST", credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ reset: true }),
                  });
                  atRes = await r1.json().catch(() => ({}));
                } catch (e) { atRes = { error: e.message }; }
                try {
                  const r2 = await fetch("/api/sync/merchrules", {
                    method: "POST", credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({}),
                  });
                  mrRes = await r2.json().catch(() => ({}));
                } catch (e) { mrRes = { error: e.message }; }
                const at = atRes.error ? `Airtable: ${atRes.error}` : `Airtable: ${atRes.synced || 0} клиентов, оплата ${atRes.payment_updated || 0}`;
                const mr = mrRes.error ? `Merchrules: ${mrRes.error}` : `Merchrules: ${mrRes.clients_synced || mrRes.synced || 0} клиентов, задач ${mrRes.tasks_synced || 0}`;
                alert(`Готово:\n  ${at}\n  ${mr}`);
                location.reload();
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

            {/* portfolio pulse — chart */}
            <Card title="Пульс портфеля · GMV 30 дней" action={
              <div style={{ display: "flex", gap: 6 }}>
                <Btn size="s" kind="ghost">7д</Btn>
                <Btn size="s" kind="dim">30д</Btn>
                <Btn size="s" kind="ghost">квартал</Btn>
              </div>
            }>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 220px", gap: 20, alignItems: "stretch" }}>
                <div>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 14 }}>
                    <div style={{ fontSize: 38, fontWeight: 500, letterSpacing: "-0.03em", lineHeight: 1, color: "var(--ink-9)" }}>
                      {_formatGmv(totalGmv)}
                    </div>
                    <div className="mono" style={{ fontSize: 13, color: "var(--ink-5)", fontWeight: 500 }}>
                      {CL.length} {CL.length === 1 ? "клиент" : (CL.length < 5 ? "клиента" : "клиентов")}
                    </div>
                  </div>
                  <BigSpark/>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8, borderLeft: "1px solid var(--line)", paddingLeft: 18 }}>
                  {buckets.map((r, i) => (
                    <div key={i}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                        <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{r.label}</span>
                        <span style={{ fontSize: 12, fontWeight: 500, color: "var(--ink-8)" }}>{_formatGmv(r.rub)}</span>
                      </div>
                      <Progress value={pctOf(r.rub)} tone={r.color} h={3}/>
                    </div>
                  ))}
                </div>
              </div>
            </Card>

            {/* tools + jobs */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
              <Card title="Инструменты" action={
                <a className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>
                  {`${TOOLS.filter(t=>t.ok).length}/${TOOLS.length} online`}
                </a>
              }>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {TOOLS.map((t, i) => {
                    const cfg = t.configured !== false;
                    const state = t.ok ? "online" : (cfg ? "offline" : "не настроено");
                    const color = t.ok ? "var(--ok)" : (cfg ? "var(--ink-5)" : "var(--warn)");
                    return (
                      <div key={i} style={{
                        display: "flex", alignItems: "center", gap: 10,
                        padding: "9px 10px",
                        background: "var(--ink-1)",
                        borderRadius: 4,
                        borderLeft: `2px solid ${t.ok ? "var(--ok)" : (cfg ? "var(--ink-3)" : "var(--warn)")}`,
                      }}>
                        <div style={{
                          width: 22, height: 22, borderRadius: 3,
                          background: "var(--ink-3)", display: "flex",
                          alignItems: "center", justifyContent: "center",
                          color: "var(--ink-7)", flexShrink: 0,
                        }}>
                          <I.link size={12}/>
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 12.5, fontWeight: 500, color: "var(--ink-8)" }}>{t.name}</div>
                          <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.detail}</div>
                        </div>
                        <div style={{ textAlign: "right" }}>
                          <div className="mono" style={{ fontSize: 10.5, color }}>
                            {t.ok ? "● online" : (cfg ? "○ offline" : "◌ не настр.")}
                          </div>
                          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)" }}>{t.sync}</div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </Card>

              <Card title="Автоматизация · cron">
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {JOBS.map((j, i) => (
                    <div key={i} style={{
                      display: "flex", alignItems: "center", gap: 10,
                      padding: "8px 4px",
                      borderBottom: i === JOBS.length - 1 ? "none" : "1px solid var(--line-soft)",
                    }}>
                      <span style={{
                        width: 6, height: 6, borderRadius: 999,
                        background: j.ok ? "var(--ok)" : "var(--critical)",
                        boxShadow: j.ok ? "0 0 6px var(--ok)" : "0 0 6px var(--critical)",
                        flexShrink: 0,
                      }}/>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 12.5, color: "var(--ink-8)" }}>{j.name}</div>
                        <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)" }}>{j.schedule}</div>
                      </div>
                      <div className="mono" style={{ fontSize: 10.5, color: j.ok ? "var(--ink-6)" : "var(--critical)" }}>
                        {j.last}
                      </div>
                      <button style={{ background: "none", border: 0, cursor: "pointer", color: "var(--ink-6)", padding: 2 }}>
                        <I.play size={11}/>
                      </button>
                    </div>
                  ))}
                </div>
                <div style={{
                  marginTop: 12, padding: 10,
                  background: "var(--ink-1)",
                  border: "1px dashed var(--line)",
                  borderRadius: 4,
                  display: "flex", alignItems: "center", gap: 10,
                }}>
                  <I.link size={14} stroke="var(--ink-6)"/>
                  <code className="mono" style={{ fontSize: 10.5, color: "var(--ink-7)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    hub.amteam.ops/webhook/merchrules
                  </code>
                  <button style={{
                    background: "var(--ink-3)", border: "1px solid var(--line)",
                    borderRadius: 3, padding: "3px 7px", cursor: "pointer",
                    color: "var(--ink-7)",
                  }}>
                    <I.copy size={12}/>
                  </button>
                </div>
              </Card>
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

          </div>
        </div>
      </div>
    </div>
  );
}

// ── Big sparkline/area chart ──────────────────────────────
function BigSpark() {
  const data = (typeof window !== "undefined" && window.GMV_SPARK) || [];
  const w = 520, h = 120;
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

window.PageHub = PageHub;
