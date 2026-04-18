// page_hub.jsx — Command Center

function PageHub() {
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
                <div className="mono" style={{ fontSize: 14, color: "var(--ink-8)", fontWeight: 500 }}>W16 · 14–20 апр</div>
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
            <Btn kind="ghost" size="m" icon={<I.refresh size={14}/>}>Синхронизировать</Btn>
            <Btn kind="primary" size="m" icon={<I.plus size={14}/>}>Новый клиент</Btn>
          </>
        }
      />

      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 22 }}>

        {/* ── KPI row ──────────────────────────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12 }}>
          <KPI label="Клиентов в портфеле" value="248" delta="+6" sub="за неделю" />
          <KPI label="Просрочено чекапов" value="3" tone="critical" delta="−2" sub="чем в понедельник" />
          <KPI label="Скоро чекап" value="9" tone="warn" sub="в ближайшие 7 дней" />
          <KPI label="Открытых задач" value="37" delta="−5" sub="−12% к прошлой неделе" />
          <KPI label="Менеджеров онлайн" value="12" unit="/ 14" sub="команда tier-1 + 2" />
        </div>

        {/* ── Main grid ────────────────────────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 18 }}>

          {/* LEFT COLUMN */}
          <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0 }}>

            {/* signals board */}
            <Card title="Сигналы — требуют внимания" action={
              <span className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>
                обновлено 42с назад
              </span>
            }>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                {[
                  { tone: "critical", title: "Aura Beauty", note: "GMV −18% за 7д, чекап просрочен на 2 дня", icon: "flame", meta: "A · с 2022" },
                  { tone: "critical", title: "Nextfood Retail", note: "3 открытые задачи в статусе blocked", icon: "alert", meta: "B+ · tier-1" },
                  { tone: "warn", title: "Kitchen Garden", note: "Договор не подписан, стартуют 22 апр", icon: "doc", meta: "B · onboarding" },
                  { tone: "warn", title: "Gemini Shop", note: "Клиент давно не в сети · 12 дн", icon: "eye", meta: "B+ · активный" },
                ].map((s, i) => {
                  const Ic = I[s.icon];
                  return (
                    <div key={i} style={{
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
                    <div style={{ fontSize: 38, fontWeight: 500, letterSpacing: "-0.03em", lineHeight: 1, color: "var(--ink-9)" }}>₽ 58.4м</div>
                    <div className="mono" style={{ fontSize: 13, color: "var(--ok)", fontWeight: 500 }}>+8.2% ↗</div>
                  </div>
                  <BigSpark/>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8, borderLeft: "1px solid var(--line)", paddingLeft: 18 }}>
                  {[
                    { label: "A+ / A", value: "₽ 34.2м", pct: 59, color: "signal" },
                    { label: "B / B+",  value: "₽ 16.8м", pct: 29, color: "info" },
                    { label: "C",       value: "₽ 6.1м",  pct: 10, color: "warn" },
                    { label: "NEW",     value: "₽ 1.3м",  pct: 2,  color: "ok" },
                  ].map((r, i) => (
                    <div key={i}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                        <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{r.label}</span>
                        <span style={{ fontSize: 12, fontWeight: 500, color: "var(--ink-8)" }}>{r.value}</span>
                      </div>
                      <Progress value={r.pct} tone={r.color} h={3}/>
                    </div>
                  ))}
                </div>
              </div>
            </Card>

            {/* tools + jobs */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
              <Card title="Инструменты" action={<a className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>5/6 online</a>}>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {TOOLS.map((t, i) => (
                    <div key={i} style={{
                      display: "flex", alignItems: "center", gap: 10,
                      padding: "9px 10px",
                      background: "var(--ink-1)",
                      borderRadius: 4,
                      borderLeft: `2px solid ${t.ok ? "var(--ok)" : "var(--ink-3)"}`,
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
                        <div className="mono" style={{ fontSize: 10.5, color: t.ok ? "var(--ok)" : "var(--ink-5)" }}>
                          {t.ok ? "● online" : "○ offline"}
                        </div>
                        <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)" }}>{t.sync}</div>
                      </div>
                    </div>
                  ))}
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
                  Сегодня в приоритете <span style={{ color: "var(--signal)" }}>3 встречи</span> и <span style={{ color: "var(--critical)" }}>1 просроченная задача</span>. AI рекомендует сначала закрыть риск по Aura Beauty.
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
  const data = [42,44,46,45,48,50,49,52,55,54,56,58,60,59,62,63,61,65,67,66,69,71,70,73,75,72,76,78,77,80,82];
  const w = 520, h = 120;
  const min = Math.min(...data), max = Math.max(...data), rng = max - min;
  const pts = data.map((v, i) => [(i / (data.length-1)) * w, h - ((v-min)/rng) * (h-8) - 4]);
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
      {/* grid lines */}
      {[0.25, 0.5, 0.75].map(p => (
        <line key={p} x1="0" x2={w} y1={h*p} y2={h*p} stroke="var(--line-soft)" strokeDasharray="2 4"/>
      ))}
      <path d={area}  fill="url(#grad)" />
      <path d={path}  fill="none" stroke="var(--signal)" strokeWidth="1.5"/>
      {/* end dot */}
      <circle cx={pts[pts.length-1][0]} cy={pts[pts.length-1][1]} r="3" fill="var(--signal)"/>
      <circle cx={pts[pts.length-1][0]} cy={pts[pts.length-1][1]} r="7" fill="var(--signal)" fillOpacity="0.18"/>
    </svg>
  );
}

window.PageHub = PageHub;
