// extension.jsx — Chrome extension popup redesign

function ExtensionPopup({ state = "connected" }) {
  // state: "connected" | "running" | "error" | "empty"
  const W = 380;
  const statusMap = {
    connected: { tone: "ok",     title: "Подключено", sub: "синхр. 2 мин назад · 14 клиентов обновлено" },
    running:   { tone: "signal", title: "Синхронизация…", sub: "этап 3/5 · задачи → AM Hub" },
    error:     { tone: "critical", title: "Ошибка авторизации", sub: "Merchrules вернул 401" },
    empty:     { tone: "neutral", title: "Не настроено", sub: "заполните поля ниже" },
  }[state];

  const color = statusMap.tone === "ok" ? "var(--ok)" :
                statusMap.tone === "signal" ? "var(--signal)" :
                statusMap.tone === "critical" ? "var(--critical)" : "var(--ink-5)";

  return (
    <div style={{
      width: W, background: "var(--ink-1)", color: "var(--ink-8)",
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
          <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>AM Hub · Sync</div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.1em" }}>
            merchrules → hub
          </div>
        </div>
        <button style={{ background: "transparent", border: 0, color: "var(--ink-6)", cursor: "pointer" }}>
          <I.gear size={14}/>
        </button>
      </div>

      {/* status card */}
      <div style={{
        margin: 12,
        padding: "14px 14px",
        background: "var(--ink-2)",
        border: "1px solid var(--line)",
        borderLeft: `2px solid ${color}`,
        borderRadius: 4,
        display: "flex", alignItems: "center", gap: 12,
      }}>
        <div style={{
          width: 32, height: 32, borderRadius: 999,
          background: `color-mix(in oklch, ${color} 12%, transparent)`,
          border: `1px solid color-mix(in oklch, ${color} 30%, transparent)`,
          display: "flex", alignItems: "center", justifyContent: "center",
          flexShrink: 0, color,
        }}>
          {state === "running" ? <I.refresh size={16} style={{ animation: "spin 1.2s linear infinite" }}/> :
           state === "error"   ? <I.alert size={16}/> :
           state === "empty"   ? <I.puzzle size={16}/> :
                                 <I.circle_check size={16}/>}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-9)" }}>{statusMap.title}</div>
          <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", marginTop: 2 }}>{statusMap.sub}</div>
        </div>
      </div>

      {/* stats row */}
      {state === "connected" && (
        <div style={{
          margin: "0 12px 12px",
          padding: "10px 12px",
          background: "var(--ink-2)", border: "1px solid var(--line)",
          borderRadius: 4,
          display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6,
        }}>
          {[
            { l: "клиенты", v: 14, d: "+2" },
            { l: "задачи",  v: 37, d: "+5" },
            { l: "события", v: 128, d: "+14" },
          ].map((s, i) => (
            <div key={i} style={{
              textAlign: "center",
              borderRight: i === 2 ? "none" : "1px solid var(--line-soft)",
            }}>
              <div style={{ fontSize: 20, fontWeight: 500, color: "var(--ink-9)", letterSpacing: "-0.02em", fontFamily: "var(--f-mono)" }}>{s.v}</div>
              <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{s.l}</div>
              <div className="mono" style={{ fontSize: 10, color: "var(--ok)", marginTop: 1 }}>{s.d}</div>
            </div>
          ))}
        </div>
      )}

      {/* fields */}
      <div style={{ padding: "0 14px 14px" }}>
        <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.1em", marginTop: 4, marginBottom: 10 }}>
          ── учётные данные
        </div>

        {[
          { label: "Merchrules · логин", val: "anna.sokolova@company.ru", type: "text", icon: "link" },
          { label: "Merchrules · пароль", val: "••••••••••••", type: "password", icon: "lock" },
          { label: "AM Hub · URL",  val: "hub.amteam.ops",  type: "text", icon: "link" },
          { label: "AM Hub · токен", val: "••••••• j7f2", type: "password", icon: "lock" },
        ].map((f, i) => (
          <div key={i} style={{ marginBottom: 8 }}>
            <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 3 }}>{f.label}</div>
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "8px 10px",
              background: "var(--ink-2)", border: "1px solid var(--line)",
              borderRadius: 4,
            }}>
              {React.createElement(I[f.icon], { size: 12, stroke: "var(--ink-5)" })}
              <input type={f.type} defaultValue={f.val} style={{
                flex: 1, background: "transparent", border: 0, color: "var(--ink-8)",
                fontFamily: "var(--f-mono)", fontSize: 12, outline: "none",
              }}/>
            </div>
          </div>
        ))}

        <Btn kind="primary" full size="m" style={{ marginTop: 6 }}
             icon={<I.refresh size={14}/>}>
          Сохранить и синхронизировать
        </Btn>
        <Btn kind="ghost" full size="m" style={{ marginTop: 6 }}
             iconRight={<I.arrow_r size={13}/>}>
          Открыть AM Hub
        </Btn>
      </div>

      {/* log feed */}
      <div style={{
        padding: "10px 14px",
        background: "var(--ink-0)",
        borderTop: "1px solid var(--line-soft)",
      }}>
        <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 6 }}>
          последние события
        </div>
        {[
          { t: "12:44", msg: "merchrules: 14 клиентов синхр.", tone: "ok" },
          { t: "12:44", msg: "hub: 37 задач обновлено", tone: "ok" },
          { t: "12:29", msg: "merchrules: delta poll", tone: "neutral" },
          { t: "09:15", msg: "hub: токен обновлён", tone: "neutral" },
        ].map((l, i) => (
          <div key={i} className="mono" style={{
            display: "grid", gridTemplateColumns: "48px 1fr",
            fontSize: 10.5, color: "var(--ink-6)",
            padding: "2px 0",
          }}>
            <span style={{ color: "var(--ink-5)" }}>{l.t}</span>
            <span style={{ color: l.tone === "ok" ? "var(--ok)" : "var(--ink-7)" }}>
              {l.tone === "ok" ? "✓ " : "· "}{l.msg}
            </span>
          </div>
        ))}
      </div>

      {/* footer */}
      <div style={{
        padding: "8px 14px",
        borderTop: "1px solid var(--line-soft)",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>v 2.0 · build 224</span>
        <span className="mono" style={{ fontSize: 9.5, color: "var(--ok)" }}>● online</span>
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

window.ExtensionPopup = ExtensionPopup;
