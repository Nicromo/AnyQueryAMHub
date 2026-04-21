// extension.jsx — Chrome extension popup preview (реальные данные из window.*)

function ExtensionPopup({ state = "empty" }) {
  // Реальные данные из window.*
  const S    = (typeof window !== "undefined" && window.__SIDEBAR_STATS) || {};
  const U    = (typeof window !== "undefined" && window.__CURRENT_USER)  || {};
  const HUB  = (typeof window !== "undefined" && window.__HUB_URL)       || window.location.origin;
  const EXT  = (typeof window !== "undefined" && window.__EXTENSIONS && window.__EXTENSIONS[0]) || {};
  const version = EXT.version || "—";

  // Авто-определяем state: если есть данные — connected, иначе empty
  const autoState = (S.clientsTotal > 0) ? "connected" : state;

  const statusMap = {
    connected: { tone: "ok",       title: "Подключено",         sub: `${U.name || U.email || "—"} · AM Hub` },
    running:   { tone: "signal",   title: "Синхронизация…",     sub: "обновление данных" },
    error:     { tone: "critical", title: "Ошибка подключения", sub: "проверьте токен в настройках" },
    empty:     { tone: "neutral",  title: "Не настроено",       sub: "заполните поля ниже для подключения" },
  }[autoState] || { tone: "neutral", title: "—", sub: "—" };

  const color = {
    ok:       "var(--ok)",
    signal:   "var(--signal)",
    critical: "var(--critical)",
    neutral:  "var(--ink-5)",
  }[statusMap.tone] || "var(--ink-5)";

  // Реальные KPI из sidebar_stats
  const stats = [
    { l: "клиенты",  v: S.clientsTotal  || 0 },
    { l: "задачи",   v: S.tasksActive   || 0 },
    { l: "встречи",  v: S.meetingsUpcoming || 0 },
  ];

  return (
    <div style={{
      width: 360, background: "var(--ink-1)", color: "var(--ink-8)",
      fontFamily: "var(--f-display)", fontSize: 13,
      border: "1px solid var(--line)", borderRadius: 8,
      overflow: "hidden",
    }}>
      {/* header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "14px 16px",
        borderBottom: "1px solid var(--line-soft)",
        background: "var(--ink-0)",
      }}>
        <div style={{
          width: 26, height: 26,
          background: "var(--signal)", color: "var(--ink-0)",
          borderRadius: 4,
          display: "flex", alignItems: "center", justifyContent: "center",
          fontFamily: "var(--f-mono)", fontWeight: 700, fontSize: 13, letterSpacing: -0.5,
        }}>A</div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>AM Hub</div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.1em" }}>
            Sync · Checkup · Tokens
          </div>
        </div>
        <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
          v {version}
        </div>
      </div>

      {/* status card */}
      <div style={{
        margin: 12,
        padding: "12px 14px",
        background: "var(--ink-2)",
        border: "1px solid var(--line)",
        borderLeft: `2px solid ${color}`,
        borderRadius: 4,
        display: "flex", alignItems: "center", gap: 12,
      }}>
        <div style={{
          width: 30, height: 30, borderRadius: 999, flexShrink: 0,
          background: `color-mix(in oklch, ${color} 12%, transparent)`,
          border: `1px solid color-mix(in oklch, ${color} 30%, transparent)`,
          display: "flex", alignItems: "center", justifyContent: "center",
          color,
        }}>
          {autoState === "running" ? <I.refresh size={15}/> :
           autoState === "error"   ? <I.alert size={15}/> :
           autoState === "empty"   ? <I.puzzle size={15}/> :
                                     <I.circle_check size={15}/>}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-9)" }}>{statusMap.title}</div>
          <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {statusMap.sub}
          </div>
        </div>
      </div>

      {/* stats row — только если есть реальные данные */}
      {autoState === "connected" && S.clientsTotal > 0 && (
        <div style={{
          margin: "0 12px 12px",
          padding: "10px 12px",
          background: "var(--ink-2)", border: "1px solid var(--line)",
          borderRadius: 4,
          display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6,
        }}>
          {stats.map((s, i) => (
            <div key={i} style={{
              textAlign: "center",
              borderRight: i === 2 ? "none" : "1px solid var(--line-soft)",
            }}>
              <div style={{ fontSize: 20, fontWeight: 500, color: "var(--ink-9)", letterSpacing: "-0.02em", fontFamily: "var(--f-mono)" }}>{s.v}</div>
              <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{s.l}</div>
            </div>
          ))}
        </div>
      )}

      {/* settings fields — без захардкоженных данных */}
      <div style={{ padding: "0 14px 14px" }}>
        <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.1em", marginTop: 4, marginBottom: 10 }}>
          ── учётные данные
        </div>

        {[
          { label: "Merchrules · логин", placeholder: "manager@company.ru",   type: "text",     icon: "link" },
          { label: "Merchrules · пароль", placeholder: "пароль",              type: "password", icon: "lock" },
          { label: "AM Hub · URL",        placeholder: HUB,                   type: "text",     icon: "link" },
          { label: "AM Hub · токен",      placeholder: "токен из кабинета",   type: "password", icon: "lock" },
        ].map((f, i) => (
          <div key={i} style={{ marginBottom: 8 }}>
            <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 3 }}>{f.label}</div>
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "8px 10px",
              background: "var(--ink-1)", border: "1px solid var(--line)",
              borderRadius: 4,
            }}>
              {React.createElement(I[f.icon], { size: 12, stroke: "var(--ink-5)" })}
              <input type={f.type} placeholder={f.placeholder} disabled style={{
                flex: 1, background: "transparent", border: 0,
                color: "var(--ink-5)",
                fontFamily: "var(--f-mono)", fontSize: 12, outline: "none",
                cursor: "default",
              }}/>
            </div>
          </div>
        ))}

        <div style={{ padding: "8px 10px", background: "var(--ink-1)", border: "1px dashed var(--line)", borderRadius: 4, fontSize: 11.5, color: "var(--ink-6)", lineHeight: 1.5, marginBottom: 8 }}>
          Заполните поля в самом расширении (иконка в тулбаре Chrome).
          URL и токен — скопируйте из блока справа.
        </div>

        <Btn kind="ghost" full size="m" onClick={() => window.open("/api/extension/download")}
             icon={<I.download size={14}/>}>
          Скачать расширение (.zip)
        </Btn>
      </div>

      {/* footer */}
      <div style={{
        padding: "8px 14px",
        background: "var(--ink-0)",
        borderTop: "1px solid var(--line-soft)",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
          am hub ext · v {version}
        </span>
        <span className="mono" style={{ fontSize: 9.5, color: autoState === "connected" ? "var(--ok)" : "var(--ink-5)" }}>
          ● {autoState === "connected" ? "online" : "offline"}
        </span>
      </div>
    </div>
  );
}

window.ExtensionPopup = ExtensionPopup;
