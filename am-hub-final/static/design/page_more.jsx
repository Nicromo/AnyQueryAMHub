// page_more.jsx — remaining tabs (top50, tasks, meetings, portfolio, ai, kanban, kpi, cabinet, templates, auto, roadmap, internal, extension-install, help)

// ── Reusable in-page form modal (replaces all browser prompt() calls) ────────
function FormModal({ title, fields, onSubmit, onClose, submitLabel }) {
  const initVals = {};
  fields.forEach(function(f){ initVals[f.k] = f.default || ""; });
  const [vals, setVals] = React.useState(initVals);
  const [saving, setSaving] = React.useState(false);
  const [err, setErr] = React.useState(null);

  const handleSubmit = async function(e) {
    e.preventDefault();
    setSaving(true); setErr(null);
    try { await onSubmit(vals); }
    catch(ex) { setErr(String(ex)); setSaving(false); }
  };

  return (
    <div onClick={function(e){ if (e.target === e.currentTarget) onClose(); }} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 9999,
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      <div style={{
        width: 440, maxWidth: "90vw", background: "var(--ink-1)", border: "1px solid var(--line)",
        borderRadius: 8, padding: 24, display: "flex", flexDirection: "column", gap: 18,
        boxShadow: "0 8px 40px rgba(0,0,0,0.4)",
      }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 15, fontWeight: 600, color: "var(--ink-9)" }}>{title}</span>
          <button onClick={onClose} style={{ background: "none", border: 0, color: "var(--ink-5)", cursor: "pointer", fontSize: 18, lineHeight: 1, padding: 4 }}>×</button>
        </div>
        {err && <div style={{ padding: "8px 10px", background: "color-mix(in oklch,var(--critical) 10%,transparent)", border: "1px solid color-mix(in oklch,var(--critical) 30%,transparent)", borderRadius: 4, color: "var(--critical)", fontSize: 12 }}>{err}</div>}
        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {fields.map(function(f) {
            const inputStyle = { padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-9)", fontFamily: "var(--f-mono)", fontSize: 12, outline: "none", width: "100%", boxSizing: "border-box" };
            return (
              <label key={f.k} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{f.label}{f.required && <span style={{color:"var(--critical)"}}> *</span>}</span>
                {f.type === "select" && (f.options || []).length > 10 ? (
                  // С большим числом опций — input+datalist (нативный автокомплит
                  // по подстроке). Пользователь начинает вводить 'yves' →
                  // выпадают все матчи. Хранится label, перед submit превращаем
                  // в value.
                  (function(){
                    const opts = f.options || [];
                    const listId = "dl-" + f.k;
                    const currentLabel = (opts.find(function(o){ return String(o.v) === String(vals[f.k]); }) || {}).l || vals[f.k] || "";
                    return <>
                      <input list={listId} placeholder={f.placeholder || "Начни печатать имя…"}
                        defaultValue={currentLabel}
                        onChange={function(e){
                          const typed = e.target.value;
                          const found = opts.find(function(o){ return o.l === typed; });
                          setVals(function(v){ return {...v, [f.k]: found ? String(found.v) : typed}; });
                        }}
                        style={inputStyle}/>
                      <datalist id={listId}>
                        {opts.map(function(o){ return <option key={o.v} value={o.l}/>; })}
                      </datalist>
                    </>;
                  })()
                ) : f.type === "select" ? (
                  <select value={vals[f.k]} onChange={function(e){ setVals(function(v){ return {...v,[f.k]:e.target.value}; }); }} style={inputStyle}>
                    {(f.options || []).map(function(o){ return <option key={o.v} value={o.v}>{o.l}</option>; })}
                  </select>
                ) : f.type === "textarea" ? (
                  <textarea value={vals[f.k]} onChange={function(e){ setVals(function(v){ return {...v,[f.k]:e.target.value}; }); }} placeholder={f.placeholder||""} rows={4} style={{...inputStyle, resize:"vertical"}}/>
                ) : (
                  <input type={f.type||"text"} value={vals[f.k]} onChange={function(e){ setVals(function(v){ return {...v,[f.k]:e.target.value}; }); }} placeholder={f.placeholder||""} required={f.required} style={inputStyle}/>
                )}
              </label>
            );
          })}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 4 }}>
            <Btn kind="ghost" size="m" type="button" onClick={onClose}>Отмена</Btn>
            <Btn kind="primary" size="m" type="submit" disabled={saving}>{saving ? "Сохраняю…" : (submitLabel || "Создать")}</Btn>
          </div>
        </form>
      </div>
    </div>
  );
}

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

// AI-разбор Top-50: шлёт метрики в /api/ai/analyze-top50 → получает текст.
function Top50AIAnalysis({ data, metric }) {
  const [text, setText] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState(null);

  const run = React.useCallback(async () => {
    if (!data || !metric) return;
    setLoading(true); setErr(null);
    try {
      const r = await fetch("/api/ai/analyze-top50", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ metric, months: data.months, clients: data.clients }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      setText(d.text || "");
    } catch (e) { setErr(e.message); }
    finally { setLoading(false); }
  }, [data, metric]);

  React.useEffect(() => { run(); }, [run]);

  return React.createElement(Card, {
    title: "AI-разбор метрик",
    action: React.createElement(Badge, { tone: "signal" }, loading ? "генерация…" : "auto"),
  },
    err && React.createElement("div", { style: { fontSize: 12.5, color: "var(--critical)", padding: "10px 0" } }, "Ошибка: " + err),
    !err && !loading && !text && React.createElement("div", { style: { fontSize: 12.5, color: "var(--ink-6)", padding: "10px 0" } }, "Нет данных для анализа."),
    !err && text && React.createElement("div", { style: { fontSize: 13, color: "var(--ink-8)", lineHeight: 1.6, whiteSpace: "pre-wrap", padding: "6px 0" } }, text),
    React.createElement("div", { style: { display: "flex", gap: 8, marginTop: 8 } },
      React.createElement(Btn, { size: "s", kind: "ghost", onClick: run }, "Обновить"),
    ),
  );
}

function PageTop50() {
  // Живые данные из Google Sheets (лист «Актуальные метрики и список топ 50»).
  // Эндпоинт /api/top50/metrics возвращает {months, metrics, clients}.
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [metricIdx, setMetricIdx] = React.useState(0);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/top50/metrics", { credentials: "include" });
        const d = await r.json();
        if (cancelled) return;
        if (d.error) setErr(d.error);
        else setData(d);
      } catch (e) { if (!cancelled) setErr(e.message); }
    })();
    return () => { cancelled = true; };
  }, []);
  const loading = !data && !err;
  const months = (data && data.months) || [];
  const metrics = (data && data.metrics) || [];
  const clients = (data && data.clients) || [];
  const activeMetric = metrics[metricIdx] || metrics[0] || "";

  // Форматер балла: 0.9753 → "0.975"
  const fmt = v => (v == null ? "—" : (typeof v === "number" ? v.toFixed(3).replace(/\.?0+$/, "") : String(v)));
  const toneOf = v => (v == null ? "neutral" : v >= 0.9 ? "ok" : v >= 0.75 ? "warn" : "critical");

  return (
    <div>
      <TopBar breadcrumbs={["am hub","top-50"]} title="Top-50 · метрики качества поиска"
        subtitle={data ? `Лист «Актуальные метрики и список топ 50» · ${clients.length} клиентов · обновлено ${data.fetched_at}` : "Загрузка из Google Sheets…"}
        actions={<Btn kind="primary" size="m" icon={<I.download size={14}/>} onClick={() => window.print()}>PDF-отчёт</Btn>}/>
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        {err && <div style={{ padding: 16, background: "rgba(240,70,58,.08)", border: "1px solid var(--critical-dim)", borderLeft: "3px solid var(--critical)", borderRadius: 4, color: "var(--critical)", fontSize: 12.5 }}>
          Ошибка загрузки: {err}
        </div>}
        {loading && <div style={{ padding: 24, textAlign: "center", color: "var(--ink-6)" }}>Загрузка метрик…</div>}

        {!loading && !err && (
          <>
            {/* Переключатель метрики */}
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {metrics.map((m, i) => (
                <button key={m} onClick={() => setMetricIdx(i)} style={{
                  padding: "7px 12px",
                  background: i === metricIdx ? "var(--signal)" : "var(--ink-2)",
                  color: i === metricIdx ? "var(--ink-0)" : "var(--ink-7)",
                  border: `1px solid ${i === metricIdx ? "var(--signal)" : "var(--line)"}`,
                  borderRadius: 4, fontFamily: "var(--f-mono)", fontSize: 11,
                  textTransform: "uppercase", letterSpacing: "0.06em", cursor: "pointer",
                }}>{m}</button>
              ))}
            </div>

            {/* AI-разбор раздела — шлём данные в /api/ai/analyze-top50 */}
            <Top50AIAnalysis data={data} metric={activeMetric}/>

            <Card title={`${activeMetric} · по месяцам`}>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ background: "var(--ink-1)", position: "sticky", top: 0 }}>
                      <th style={{ padding: "10px 12px", textAlign: "left", fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", borderBottom: "1px solid var(--line)" }}>Клиент</th>
                      {months.map(m => <th key={m} style={{ padding: "10px 8px", textAlign: "right", fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", borderBottom: "1px solid var(--line)" }}>{m}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {clients.length === 0 && (
                      <tr><td colSpan={months.length + 1} style={{ padding: 28, textAlign: "center", color: "var(--ink-6)" }}>
                        Нет клиентов в скоупе. Проверь синк Airtable и колонку «Сайт» в Google Sheets.
                      </td></tr>
                    )}
                    {clients.map((c, i) => {
                      const mdata = (c.metrics && c.metrics[activeMetric]) || {};
                      const vals = months.map(m => mdata[m]);
                      const last = vals.filter(v => v != null).slice(-1)[0];
                      const prev = vals.filter(v => v != null).slice(-2, -1)[0];
                      const delta = (last != null && prev != null && prev > 0) ? (last - prev) / prev : null;
                      return (
                        <tr key={i} style={{ borderBottom: "1px solid var(--line-soft)" }}>
                          <td style={{ padding: "8px 12px", color: "var(--ink-9)", fontWeight: 500 }}>
                            {c.name}
                            {delta != null && (
                              <span className="mono" style={{ marginLeft: 8, fontSize: 10, color: delta >= 0 ? "var(--ok)" : "var(--critical)" }}>
                                {delta >= 0 ? "+" : ""}{(delta * 100).toFixed(1)}%
                              </span>
                            )}
                          </td>
                          {months.map((m, j) => {
                            const v = mdata[m];
                            const tone = toneOf(v);
                            const bg = v == null ? "transparent"
                              : tone === "ok" ? "rgba(120,200,120,.08)"
                              : tone === "warn" ? "rgba(240,180,60,.08)"
                              : "rgba(240,70,58,.08)";
                            return (
                              <td key={j} style={{ padding: "8px 8px", textAlign: "right", fontFamily: "var(--f-mono)", fontSize: 11.5, color: "var(--ink-8)", background: bg }}>
                                {fmt(v)}
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}

// ── Tasks ─────────────────────────────────────────────────
function PageTasks() {
  const [filter, setFilter] = React.useState("mine");
  const ALL_TK = (typeof window !== "undefined" && window.TASKS) || [];
  const U = (typeof window !== "undefined" && window.__CURRENT_USER) || {};
  const CL_TASKS = (typeof window !== "undefined" && window.CLIENTS) || [];
  const [taskModal, setTaskModal] = React.useState(null); // null or {column}
  // Кастомные колонки (пользовательские доски). Храним в localStorage.
  // Структура: [{ key: "review", title: "На проверке", tone: "info" }, ...]
  const CUSTOM_KEY = "amhub_kanban_cols_v1";
  const [customCols, setCustomCols] = React.useState(() => {
    try { return JSON.parse(localStorage.getItem(CUSTOM_KEY) || "[]"); }
    catch (_) { return []; }
  });
  const saveCustom = (next) => {
    setCustomCols(next);
    try { localStorage.setItem(CUSTOM_KEY, JSON.stringify(next)); } catch (_) {}
  };
  const addColumn = () => {
    const title = (window.prompt("Название колонки:") || "").trim();
    if (!title) return;
    const key = "c_" + Date.now().toString(36);
    saveCustom([...customCols, { key, title, tone: "info" }]);
  };
  const removeColumn = async (key) => {
    if (!await appConfirm("Удалить колонку? Задачи останутся, но без колонки.")) return;
    saveCustom(customCols.filter(c => c.key !== key));
  };

  // When filter="mine", show tasks assigned to current user (by team/email); "all" shows all
  const TK = filter === "mine" && U.email
    ? ALL_TK.filter(t => !t.team || t.team === U.email || t.team === U.name)
    : ALL_TK;

  // Группировка:
  //  1) task_type совпадает с key кастомной колонки → в неё
  //  2) status === "in_progress" → «В работе»
  //  3) по due → Просрочено / Сегодня / Бэклог
  const customBuckets = {}; customCols.forEach(c => { customBuckets[c.key] = []; });
  const today = [], backlog = [], overdue = [], inProgress = [];
  const customKeys = new Set(customCols.map(c => c.key));
  TK.forEach(t => {
    if (t.task_type && customKeys.has(t.task_type)) { customBuckets[t.task_type].push(t); return; }
    if (t.status === "in_progress") { inProgress.push(t); return; }
    const due = (t.due || "").toLowerCase();
    if (due.indexOf("просроч") !== -1) overdue.push(t);
    else if (due === "сегодня") today.push(t);
    else backlog.push(t);
  });

  const mapItem = t => ({ id: t.id, t: t.title, cl: t.client, pr: t.priority, raw: t });
  const standardCols = [
    { title: "Просрочено", key: "overdue",     tone: "critical", items: overdue,    removable: false },
    { title: "Сегодня",    key: "today",       tone: "signal",   items: today,      removable: false },
    { title: "В работе",   key: "in_progress", tone: "warn",     items: inProgress, removable: false },
    { title: "Бэклог",     key: "plan",        tone: "neutral",  items: backlog,    removable: false },
  ];
  const userCols = customCols.map(c => ({
    title: c.title, key: c.key, tone: c.tone || "info",
    items: customBuckets[c.key] || [], removable: true,
  }));
  const cols = [...standardCols, ...userCols].map(c => ({
    ...c, count: c.items.length, items: c.items.slice(0, 20).map(mapItem),
  }));

  // Детали задачи — модалка
  const [detail, setDetail] = React.useState(null);

  // Drag-and-drop: перемещение задачи в колонку.
  //  standard keys меняют status ± due_date; кастомные — task_type.
  const [dragOver, setDragOver] = React.useState(null);
  const onDragStart = (e, taskId) => {
    e.dataTransfer.setData("text/plain", String(taskId));
    e.dataTransfer.effectAllowed = "move";
  };
  const onDrop = async (e, colKey) => {
    e.preventDefault();
    setDragOver(null);
    const taskId = parseInt(e.dataTransfer.getData("text/plain"), 10);
    if (!taskId) return;

    // PUT /api/tasks/{id} принимает status, due_date, task_type.
    const body = {};
    const now = new Date();
    if (colKey === "overdue") {
      // Вчера 23:59 — чтобы попала в «Просрочено».
      const d = new Date(now); d.setDate(d.getDate() - 1); d.setHours(23, 59, 0, 0);
      body.status = "plan"; body.due_date = d.toISOString();
      body.task_type = null;
    } else if (colKey === "today") {
      const d = new Date(now); d.setHours(23, 59, 0, 0);
      body.status = "plan"; body.due_date = d.toISOString();
      body.task_type = null;
    } else if (colKey === "in_progress") {
      body.status = "in_progress";
      body.task_type = null;
    } else if (colKey === "plan") {
      const d = new Date(now); d.setDate(d.getDate() + 7); d.setHours(18, 0, 0, 0);
      body.status = "plan"; body.due_date = d.toISOString();
      body.task_type = null;
    } else if (customKeys.has(colKey)) {
      body.task_type = colKey;
    } else {
      return;
    }

    try {
      const r = await fetch(`/api/tasks/${taskId}`, {
        method: "PUT", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      if (typeof appToast === "function") appToast("Перемещено", "ok");
      location.reload();
    } catch (err) {
      if (typeof appToast === "function") appToast("Ошибка: " + err.message, "error");
    }
  };
  const totalActive = TK.length;
  return (
    <div>
      {taskModal && (
        <FormModal title={"Новая задача · " + (taskModal.column || "бэклог")}
          fields={[
            { k: "title",     label: "Задача",   required: true, placeholder: "Написать план развития клиента" },
            { k: "client_id", label: "Клиент",   type: "select",
              options: [{ v:"", l:"— без клиента —" }].concat(CL_TASKS.map(function(c){ return { v: String(c.id), l: c.name }; })) },
            { k: "priority",  label: "Приоритет", type: "select",
              options: [{v:"low",l:"low"},{v:"med",l:"med"},{v:"high",l:"high"},{v:"critical",l:"critical"}], default: "med" },
            { k: "due",       label: "Срок (дней от сегодня)", type: "number", default: "3", placeholder: "3" },
          ]}
          onClose={() => setTaskModal(null)}
          onSubmit={async function(vals) {
            const dueDate = new Date(); dueDate.setDate(dueDate.getDate() + parseInt(vals.due || 3, 10));
            const body = { title: vals.title, priority: vals.priority || "med", due_date: dueDate.toISOString() };
            if (vals.client_id) body.client_id = parseInt(vals.client_id, 10);
            const r = await fetch("/api/tasks", {
              method: "POST", credentials: "include",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            });
            if (!r.ok) throw new Error(await r.text());
            setTaskModal(null); location.reload();
          }}
        />
      )}
      <TopBar breadcrumbs={["am hub","задачи"]} title="Задачи · канбан"
        subtitle={`${totalActive} активных · ${overdue.length} просрочено · ${today.length} на сегодня`}
        actions={<><Btn kind={filter === "mine" ? "primary" : "ghost"} size="m" onClick={() => setFilter("mine")}>Мои</Btn><Btn kind={filter === "all" ? "primary" : "dim"} size="m" onClick={() => setFilter("all")}>Вся команда</Btn><Btn kind="primary" size="m" icon={<I.plus size={14}/>} onClick={() => {
          // Клик на "+" открывает глобальную модалку из shell (FAB всегда в DOM)
          document.querySelector('button[title="Новая задача"]')?.click();
        }}>Задача</Btn></>}/>
      <div style={{ padding: "22px 28px 40px" }}>
        <div style={{ display: "flex", gap: 14, alignItems: "flex-start", overflowX: "auto", paddingBottom: 8 }}>
          {cols.map((c, i) => (
            <div key={c.key || i}
              onDragOver={(e) => { e.preventDefault(); setDragOver(c.key); }}
              onDragLeave={() => setDragOver(null)}
              onDrop={(e) => onDrop(e, c.key)}
              style={{
                flex: "0 0 280px",
                background: "var(--ink-2)",
                border: `1px solid ${dragOver === c.key ? "var(--signal)" : "var(--line)"}`,
                borderRadius: 6,
                display: "flex", flexDirection: "column",
                minHeight: 480,
                transition: "border-color .15s",
              }}>
              <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--line-soft)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 6, height: 6, borderRadius: 999, background: c.tone==="signal"?"var(--signal)":c.tone==="warn"?"var(--warn)":c.tone==="critical"?"var(--critical)":c.tone==="info"?"var(--info,var(--signal))":"var(--ink-5)" }}/>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>{c.title}</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span className="mono" style={{ fontSize: 11, color: "var(--ink-5)" }}>{c.count}</span>
                  {c.removable && (
                    <button onClick={() => removeColumn(c.key)}
                      title="Удалить колонку"
                      style={{ background: "none", border: 0, color: "var(--ink-5)", cursor: "pointer", fontSize: 13, padding: 0, lineHeight: 1 }}>×</button>
                  )}
                </div>
              </div>
              <div style={{ padding: 10, display: "flex", flexDirection: "column", gap: 8, flex: 1 }}>
                {c.items.map((it) => (
                  <div key={it.id || it.t}
                    draggable={!!it.id}
                    onDragStart={(e) => onDragStart(e, it.id)}
                    onClick={() => { if (it.raw || it.id) setDetail(it.raw || { id: it.id, title: it.t, client: it.cl, priority: it.pr }); }}
                    style={{
                      padding: 12,
                      background: "var(--ink-1)",
                      border: "1px solid var(--line)",
                      borderRadius: 4,
                      cursor: it.id ? "grab" : "default",
                      userSelect: "none",
                    }}>
                    <div style={{ fontSize: 12.5, color: "var(--ink-8)", lineHeight: 1.4, marginBottom: 8 }}>{it.t}</div>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <span className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{it.cl}</span>
                      <Badge tone={it.pr==="critical"?"critical":it.pr==="high"?"warn":it.pr==="med"?"info":"neutral"} dot>{it.pr}</Badge>
                    </div>
                  </div>
                ))}
                <button onClick={() => setTaskModal({ column: c.title, colKey: c.key })} style={{ marginTop: 4, padding: "8px 10px", background: "transparent", border: "1px dashed var(--line)", borderRadius: 4, color: "var(--ink-5)", cursor: "pointer", fontFamily: "var(--f-mono)", fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase" }}>+ добавить</button>
              </div>
            </div>
          ))}
          {/* Кнопка «+ Колонка» — добавляет кастомную доску */}
          <button onClick={addColumn}
            style={{
              flex: "0 0 280px", minHeight: 60,
              background: "transparent",
              border: "1px dashed var(--line)",
              borderRadius: 6,
              color: "var(--ink-5)",
              cursor: "pointer",
              fontFamily: "var(--f-mono)", fontSize: 11,
              letterSpacing: "0.08em", textTransform: "uppercase",
            }}>+ Колонка</button>
        </div>
      </div>
      {detail && <TaskDetailModal task={detail} onClose={() => setDetail(null)} onReload={() => location.reload()}/>}
    </div>
  );
}

// ── TaskDetailModal — модалка с деталями задачи + смена статуса ────────────

function TaskDetailModal({ task, onClose, onReload }) {
  const [full, setFull] = React.useState(task);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (!task.id) return;
    (async () => {
      try {
        const r = await fetch("/api/tasks/" + task.id, { credentials: "include" });
        if (r.ok) { const d = await r.json(); setFull({...task, ...(d.task || d)}); }
      } catch (_) {}
    })();
  }, [task.id]);

  async function updateStatus(newStatus) {
    setBusy(true);
    try {
      const r = await fetch(`/api/tasks/${task.id}/status`, {
        method: "PATCH", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      appToast("Статус обновлён: " + newStatus, "ok");
      onClose(); onReload();
    } catch (e) { appToast("Ошибка: " + e.message, "error"); }
    setBusy(false);
  }

  async function removeTask() {
    if (!await appConfirm("Удалить задачу?")) return;
    setBusy(true);
    try {
      const r = await fetch("/api/tasks/" + task.id, { method: "DELETE", credentials: "include" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      appToast("Удалено", "ok");
      onClose(); onReload();
    } catch (e) { appToast("Ошибка: " + e.message, "error"); }
    setBusy(false);
  }

  return React.createElement("div", {
    style: {
      position: "fixed", inset: 0, zIndex: 9998,
      background: "rgba(0,0,0,.55)", backdropFilter: "blur(3px)",
      display: "flex", alignItems: "center", justifyContent: "center",
      padding: 24,
    },
    onClick: (e) => { if (e.target === e.currentTarget) onClose(); },
  },
    React.createElement("div", {
      style: {
        background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 8,
        width: "100%", maxWidth: 620, maxHeight: "80vh", overflowY: "auto",
        boxShadow: "0 24px 64px rgba(0,0,0,.5)",
      },
    },
      React.createElement("div", { style: { padding: 18, borderBottom: "1px solid var(--line)", display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 } },
        React.createElement("div", null,
          React.createElement("div", { style: { fontSize: 15, fontWeight: 600, color: "var(--ink-9)" } }, full.title || "Задача"),
          React.createElement("div", { className: "mono", style: { fontSize: 11, color: "var(--ink-5)", marginTop: 4 } },
            `#${full.id} · ${full.client || "—"} · ${full.status || "—"} · ${full.due || "—"}`),
        ),
        React.createElement("button", { onClick: onClose, style: { background: "none", border: 0, color: "var(--ink-6)", fontSize: 20, cursor: "pointer" } }, "✕"),
      ),
      React.createElement("div", { style: { padding: 18, fontSize: 13, color: "var(--ink-8)", lineHeight: 1.6, whiteSpace: "pre-wrap" } },
        full.description || React.createElement("span", { style: { color: "var(--ink-6)" } }, "Без описания"),
      ),
      React.createElement("div", { style: { padding: "14px 18px", borderTop: "1px solid var(--line)", display: "flex", gap: 8, flexWrap: "wrap" } },
        ["plan", "in_progress", "review", "done", "blocked"].map(s =>
          React.createElement(Btn, {
            key: s, size: "s",
            kind: full.status === s ? "primary" : "ghost",
            onClick: () => updateStatus(s), disabled: busy,
          }, s)
        ),
        React.createElement("div", { style: { marginLeft: "auto", display: "flex", gap: 8 } },
          full.client_id && React.createElement(Btn, { size: "s", kind: "ghost",
            onClick: () => { window.location.href = "/design/client/" + full.client_id; } }, "К клиенту →"),
          React.createElement(Btn, { size: "s", kind: "ghost", onClick: removeTask, disabled: busy }, "🗑"),
        ),
      ),
    ),
  );
}
window.TaskDetailModal = TaskDetailModal;

// ── Meetings ──────────────────────────────────────────────
function PageMeetings() {
  const MT = (typeof window !== "undefined" && window.MEETINGS) || [];
  const CL = (typeof window !== "undefined" && window.CLIENTS) || [];
  const U = (typeof window !== "undefined" && window.__CURRENT_USER) || {};
  const [meetModal, setMeetModal] = React.useState(null); // null or {type, label, dur}
  const [scope, setScope] = React.useState(() => (typeof localStorage !== "undefined" ? (localStorage.getItem("amhub_meetings_scope") || "all") : "all"));
  const [tab, setTab] = React.useState(() => { try { return localStorage.getItem("amhub_meetings_tab") || "list"; } catch (_) { return "list"; }});
  const setTabAndStore = (t) => { setTab(t); try { localStorage.setItem("amhub_meetings_tab", t); } catch (_) {} };

  const setScopeAndStore = (s) => {
    setScope(s);
    try { localStorage.setItem("amhub_meetings_scope", s); } catch (e) {}
  };

  // Маппинг из window.MEETINGS (server-shape: {when, day, client, type, seg, mood, is_past, manager_email})
  // в UI-shape: {d, cl, kind, seg, who, ch, mood, is_past}.
  const meets = MT.map(m => ({
    d: `${m.day || "—"} · ${m.when || ""}`.trim(),
    cl: m.client || "—",
    kind: m.type || "sync",
    seg: m.seg || "—",
    who: "—",   // attendees не пробрасываются с сервера (JSONB поле)
    ch: "",     // channel не в шаблоне сервера
    mood: m.mood || "ok",
    is_past: !!m.is_past,
    manager_email: m.manager_email || "",
  })).filter(m => scope === "all" ? true : (m.manager_email === (U.email || "")));

  const upcoming = meets.filter(m => !m.is_past);
  // past уже приходит с сервера отсортированным desc; сохраняем порядок
  const past = meets.filter(m => m.is_past);

  // Агрегаты справа (по upcoming)
  const total = upcoming.length;
  const withRisk = upcoming.filter(m => m.mood === "risk").length;
  const withOk = upcoming.filter(m => m.mood === "ok").length;
  return (
    <div>
      <TopBar breadcrumbs={["am hub","встречи"]} title="Встречи"
        subtitle={total > 0 ? `${total} предстоящих · ${past.length} прошедших · ${withRisk} с риском · ${withOk} ок` : (past.length > 0 ? `0 предстоящих · ${past.length} прошедших` : "Нет встреч")}
        actions={<>
          <Btn kind={tab === "list" ? "primary" : "ghost"} size="m" onClick={() => setTabAndStore("list")}>Список</Btn>
          <Btn kind={tab === "calendar" ? "primary" : "ghost"} size="m" onClick={() => setTabAndStore("calendar")}>Календарь</Btn>
          <div style={{ width: 8 }}/>
          <Btn kind={scope === "all" ? "primary" : "ghost"} size="m" onClick={() => setScopeAndStore("all")}>Все</Btn>
          <Btn kind={scope === "mine" ? "primary" : "ghost"} size="m" onClick={() => setScopeAndStore("mine")}>Мои</Btn>
          <Btn kind="primary" size="m" icon={<I.plus size={14}/>} onClick={() => setMeetModal({ type: "sync", label: "Встреча", dur: 30 })}>Запланировать</Btn>
        </>}/>
      {meetModal && (
        <FormModal
          title={"Запланировать · " + meetModal.label}
          fields={[
            { k: "client_id", label: "Клиент", required: true, type: "select",
              options: [{ v: "", l: "— выберите клиента —" }].concat(CL.map(function(c){ return { v: String(c.id), l: c.name }; })) },
            { k: "date", label: "Дата и время", required: true, type: "datetime-local",
              default: new Date(Date.now() + 24*3600*1000).toISOString().slice(0,16) },
          ]}
          onClose={() => setMeetModal(null)}
          onSubmit={async function(vals) {
            if (!vals.client_id) throw new Error("Выберите клиента");
            const r = await fetch("/api/meetings", {
              method: "POST", credentials: "include",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ client_id: parseInt(vals.client_id, 10), type: meetModal.type,
                title: meetModal.label, duration: meetModal.dur, date: vals.date }),
            });
            if (!r.ok) throw new Error(await r.text());
            setMeetModal(null); location.reload();
          }}
          submitLabel="Запланировать"
        />
      )}
      {tab === "calendar" && (
        <div style={{ padding: "22px 28px 40px" }}>
          <MeetingsCalendar meetings={MT.filter(m => scope === "all" ? true : (m.manager_email || "") === (U.email || ""))}/>
        </div>
      )}
      {tab === "list" && (
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 320px", gap: 18 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <Card title="Предстоящие">
            {upcoming.length === 0 && (
              <div style={{ padding: "28px 10px", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
                Нет предстоящих встреч в вашем календаре.
              </div>
            )}
            {upcoming.map((m, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "150px 1fr 140px 90px 40px", gap: 14, padding: "14px 6px", borderBottom: i===upcoming.length-1?"none":"1px solid var(--line-soft)", alignItems: "center" }}>
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

          <Card title="Прошедшие · 60 дней">
            {past.length === 0 && (
              <div style={{ padding: "28px 10px", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
                За последние 60 дней встреч не было.
              </div>
            )}
            {past.map((m, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "150px 1fr 140px 90px 40px", gap: 14, padding: "14px 6px", borderBottom: i===past.length-1?"none":"1px solid var(--line-soft)", alignItems: "center", opacity: 0.78 }}>
                <div>
                  <div style={{ fontSize: 12.5, color: "var(--ink-8)", fontWeight: 500 }}>{m.d.split(" · ")[0]}</div>
                  <div className="mono" style={{ fontSize: 11, color: "var(--ink-5)" }}>{m.d.split(" · ")[1]}</div>
                </div>
                <div>
                  <div style={{ fontSize: 13.5, color: "var(--ink-8)", fontWeight: 500 }}>{m.cl}</div>
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{m.kind} · {m.ch}</div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Avatar name={m.who} size={22}/>
                  <span style={{ fontSize: 12, color: "var(--ink-6)" }}>{m.who}</span>
                </div>
                <Seg value={m.seg}/>
                <I.arrow_r size={14} stroke="var(--ink-5)"/>
              </div>
            ))}
          </Card>
        </div>

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
              <div key={i} onClick={() => setMeetModal(t)}
              style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 0", borderBottom: i === a.length - 1 ? "none" : "1px solid var(--line-soft)", cursor: "pointer" }}>
                <I.cal size={14} stroke="var(--ink-6)"/>
                <span style={{ flex: 1, fontSize: 12.5, color: "var(--ink-8)" }}>{t.label}</span>
                <I.arrow_r size={12} stroke="var(--ink-5)"/>
              </div>
            ))}
          </Card>
        </div>
      </div>
      )}
    </div>
  );
}

// ── MeetingsCalendar — month view с визуализацией встреч ───────────────────
function MeetingsCalendar({ meetings }) {
  const [cursor, setCursor] = React.useState(() => {
    const d = new Date(); return { year: d.getFullYear(), month: d.getMonth() };
  });
  const [sel, setSel] = React.useState(null);

  // Индексируем встречи по дате (YYYY-MM-DD)
  const byDate = React.useMemo(() => {
    const map = {};
    for (const m of meetings || []) {
      const day = (m.day || "").trim();
      if (!day) continue;
      // server-shape: "YYYY-MM-DD" или "DD.MM.YYYY"
      let key = day;
      if (/^\d{2}\.\d{2}\.\d{4}$/.test(day)) {
        const [dd, mm, yyyy] = day.split(".");
        key = `${yyyy}-${mm}-${dd}`;
      }
      (map[key] = map[key] || []).push(m);
    }
    return map;
  }, [meetings]);

  const monthLabel = new Date(cursor.year, cursor.month, 1).toLocaleString("ru-RU", { month: "long", year: "numeric" });
  const firstOfMonth = new Date(cursor.year, cursor.month, 1);
  const lastOfMonth = new Date(cursor.year, cursor.month + 1, 0);
  const startOffset = (firstOfMonth.getDay() + 6) % 7;  // Пн=0
  const totalCells = Math.ceil((startOffset + lastOfMonth.getDate()) / 7) * 7;

  const today = new Date();
  const todayKey = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,"0")}-${String(today.getDate()).padStart(2,"0")}`;

  const prev = () => setCursor(c => c.month === 0 ? { year: c.year - 1, month: 11 } : { year: c.year, month: c.month - 1 });
  const next = () => setCursor(c => c.month === 11 ? { year: c.year + 1, month: 0 } : { year: c.year, month: c.month + 1 });
  const todayBtn = () => { const d = new Date(); setCursor({ year: d.getFullYear(), month: d.getMonth() }); };

  const cells = [];
  for (let i = 0; i < totalCells; i++) {
    const dayNum = i - startOffset + 1;
    const inMonth = dayNum >= 1 && dayNum <= lastOfMonth.getDate();
    if (!inMonth) { cells.push(null); continue; }
    const key = `${cursor.year}-${String(cursor.month+1).padStart(2,"0")}-${String(dayNum).padStart(2,"0")}`;
    cells.push({ key, day: dayNum, ms: byDate[key] || [], isToday: key === todayKey });
  }

  return React.createElement(Card, {
    title: monthLabel,
    actions: React.createElement("div", { style: { display: "flex", gap: 6 } },
      React.createElement(Btn, { kind: "ghost", size: "s", onClick: prev }, "←"),
      React.createElement(Btn, { kind: "ghost", size: "s", onClick: todayBtn }, "Сегодня"),
      React.createElement(Btn, { kind: "ghost", size: "s", onClick: next }, "→"),
    ),
  },
    React.createElement("div", {
      style: {
        display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 2,
        fontSize: 11, color: "var(--ink-6)", marginBottom: 4,
        fontFamily: "var(--f-mono)", textTransform: "uppercase",
      },
    },
      ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"].map(d =>
        React.createElement("div", { key: d, style: { padding: "4px 6px" } }, d)),
    ),
    React.createElement("div", {
      style: { display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 2 },
    },
      cells.map((c, i) => c == null
        ? React.createElement("div", { key: i, style: { minHeight: 86, background: "var(--ink-1)", opacity: 0.3 } })
        : React.createElement("div", {
            key: c.key,
            onClick: () => setSel(c),
            style: {
              minHeight: 86, padding: 6, cursor: "pointer",
              background: c.isToday ? "color-mix(in oklch, var(--signal) 12%, var(--ink-2))" : "var(--ink-2)",
              border: "1px solid var(--line-soft)",
              borderRadius: 3,
              display: "flex", flexDirection: "column", gap: 3,
            },
          },
            React.createElement("div", {
              style: { display: "flex", justifyContent: "space-between", alignItems: "baseline" },
            },
              React.createElement("span", { style: { fontSize: 12, color: c.isToday ? "var(--signal)" : "var(--ink-7)", fontWeight: c.isToday ? 600 : 400 } }, String(c.day)),
              c.ms.length > 0 && React.createElement("span", { className: "mono", style: { fontSize: 9.5, color: "var(--ink-5)" } }, `×${c.ms.length}`),
            ),
            c.ms.slice(0, 3).map((m, j) =>
              React.createElement("div", {
                key: j,
                title: `${m.when || ""} · ${m.client || ""} · ${m.type || ""}`,
                style: {
                  fontSize: 10, padding: "1px 4px",
                  background: m.mood === "risk" ? "color-mix(in oklch, var(--critical) 18%, transparent)" : "var(--ink-3)",
                  color: m.mood === "risk" ? "var(--critical)" : "var(--ink-8)",
                  borderRadius: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                },
              }, (m.when ? m.when + " " : "") + (m.client || m.type || "встреча"))),
            c.ms.length > 3 && React.createElement("div", { style: { fontSize: 10, color: "var(--ink-5)" } }, `ещё ${c.ms.length - 3}…`),
          ))
    ),

    sel && React.createElement("div", {
      style: {
        marginTop: 14, padding: 14,
        background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 6,
      },
    },
      React.createElement("div", { style: { display: "flex", justifyContent: "space-between", marginBottom: 10 } },
        React.createElement("div", { style: { fontSize: 13, fontWeight: 600 } },
          `Встречи ${sel.key}`),
        React.createElement("button", { onClick: () => setSel(null), style: { background: "none", border: 0, color: "var(--ink-5)", cursor: "pointer", fontSize: 18 } }, "×"),
      ),
      sel.ms.length === 0
        ? React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12 } }, "Нет встреч")
        : React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 6 } },
            sel.ms.map((m, j) => React.createElement("div", {
              key: j,
              style: { padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line-soft)", borderRadius: 4 },
            },
              React.createElement("div", { style: { display: "flex", gap: 8, alignItems: "center" } },
                React.createElement("span", { className: "mono", style: { fontSize: 11, color: "var(--ink-6)" } }, m.when || "—"),
                React.createElement("span", { style: { fontSize: 13, color: "var(--ink-9)", flex: 1 } }, m.client || "—"),
                React.createElement("span", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase" } }, m.type || ""),
              ),
            ))),
    ),
  );
}
window.MeetingsCalendar = MeetingsCalendar;

// ── Portfolio ─────────────────────────────────────────────
function PagePortfolio() {
  const CL = (typeof window !== "undefined" && window.CLIENTS) || [];
  const [groupBy, setGroupBy] = React.useState("segment"); // "segment" | "manager"
  const scrollTo = (id) => {
    const el = document.getElementById(id);
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

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
        actions={<>
          <Btn kind={groupBy === "segment" ? "primary" : "ghost"} size="m"
            onClick={() => { setGroupBy("segment"); scrollTo("portfolio-segments"); }}>По сегменту</Btn>
          <Btn kind={groupBy === "manager" ? "primary" : "ghost"} size="m"
            onClick={() => { setGroupBy("manager"); scrollTo("portfolio-managers"); }}>По менеджеру</Btn>
          <Btn kind="primary" size="m" icon={<I.download size={14}/>} onClick={() => window.print()}>PDF</Btn>
        </>}/>
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div id="portfolio-segments" style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 12 }}>
          {segs.map((s,i)=>(
            <div key={i} style={{ padding: 16, background: "var(--ink-2)", border: "1px solid var(--line)", borderLeft: `3px solid var(--${s.t})`, borderRadius: 6 }}>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>сегмент</div>
              <div style={{ fontSize: 28, fontWeight: 500, color: `var(--${s.t})`, letterSpacing: "-0.03em", marginTop: 4 }}>{s.l}</div>
              <div className="mono" style={{ fontSize: 12, color: "var(--ink-8)", marginTop: 6 }}>{s.n} {s.n === 1 ? "клиент" : "клиентов"}</div>
              <div className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>{s.v}</div>
            </div>
          ))}
        </div>

        <div id="portfolio-managers" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
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
  const [showHistory, setShowHistory] = React.useState(false);
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
    const history = messages.map(m => ({
      // Сервер ждёт role ∈ {"user","assistant"} — нормализуем "ai" → "assistant"
      role: m.role === "ai" ? "assistant" : m.role,
      content: m.content || m.text,
    }));
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
      setMessages(prev => [...prev, { role: "assistant", content: d.reply || d.answer || "" }]);
    } catch (e) {
      setError(e.message || "Не удалось получить ответ");
    } finally {
      setSending(false);
    }
  };

  const newSession = async () => {
    try {
      await fetch("/api/ai/chat/history", { method: "DELETE", credentials: "include" });
    } catch (e) {}
    setMessages([]); setError(null);
  };

  // Голосовой ввод через WebSpeech API (если доступен)
  const recogRef = React.useRef(null);
  const [listening, setListening] = React.useState(false);
  const toggleMic = () => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { appToast("Голосовой ввод не поддерживается этим браузером."); return; }
    if (listening) { recogRef.current?.stop(); setListening(false); return; }
    const r = new SR();
    r.lang = "ru-RU"; r.continuous = false; r.interimResults = true;
    r.onresult = (ev) => {
      let text = "";
      for (let i = 0; i < ev.results.length; i++) text += ev.results[i][0].transcript;
      setInput(text);
    };
    r.onerror = () => setListening(false);
    r.onend = () => setListening(false);
    recogRef.current = r;
    r.start(); setListening(true);
  };

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
        actions={<><Btn kind={showHistory ? "primary" : "ghost"} size="m" icon={<I.doc size={14}/>} onClick={() => setShowHistory(v => !v)}>История</Btn><Btn kind="primary" size="m" icon={<I.plus size={14}/>} onClick={newSession}>+ Новая сессия</Btn></>}/>
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
              placeholder={listening ? "Говорите…" : "Спросите о портфеле, клиенте или задаче…"}
              style={{ flex: 1, background: "transparent", border: 0, color: "var(--ink-8)", outline: "none", fontFamily: "var(--f-display)", fontSize: 13 }}/>
            <Btn size="s" kind={listening ? "primary" : "ghost"} type="button" onClick={toggleMic}
              icon={<I.mic size={12}/>} title={listening ? "Стоп" : "Голосовой ввод"}/>
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

  const [summary, setSummary] = React.useState(null);
  const [summaryErr, setSummaryErr] = React.useState(null);

  React.useEffect(() => {
    let cancelled = false;
    fetch("/api/me/kpi-summary", { credentials: "include" })
      .then(r => r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)))
      .then(d => { if (!cancelled) setSummary(d); })
      .catch(e => { if (!cancelled) setSummaryErr(e.message); });
    return () => { cancelled = true; };
  }, []);

  const kpis = [
    {
      l: "NRR",
      v: summary && summary.nrr != null ? `${summary.nrr}%` : "—",
      sub: "MRR this / prev month",
      tone: !summary || summary.nrr == null ? "neutral" : summary.nrr >= 100 ? "ok" : summary.nrr >= 90 ? "warn" : "critical",
    },
    {
      l: "NPS",
      v: summary && summary.nps != null ? String(summary.nps) : "—",
      sub: "за 90 дней",
      tone: !summary || summary.nps == null ? "neutral" : summary.nps >= 30 ? "ok" : summary.nps >= 0 ? "warn" : "critical",
    },
    {
      l: "Клиентов ок",
      v: summary ? `${summary.clients_ok}/${summary.clients_total}` : "—",
      sub: "health ≥ 70%",
      tone: "signal",
    },
    {
      l: "Просроченные встречи",
      v: summary ? String(summary.overdue_meetings) : "—",
      sub: "без followup",
      tone: summary && (summary.overdue_meetings || 0) > 0 ? "critical" : "ok",
    },
  ];

  return (
    <div>
      <TopBar breadcrumbs={["am hub","мой kpi"]} title="Мой KPI"
        subtitle={`${U.name || U.email || "Менеджер"} · ${U.role || "user"}`}/>
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        {summaryErr && (
          <div style={{ padding: "8px 12px", background: "color-mix(in oklch, var(--critical) 10%, transparent)", border: "1px solid color-mix(in oklch, var(--critical) 30%, transparent)", borderRadius: 4, color: "var(--critical)", fontSize: 12 }}>
            Ошибка загрузки KPI: {summaryErr}
          </div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12 }}>
          {kpis.map((k,i)=>(
            <div key={i} style={{ padding: 18, background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6 }}>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{k.l}</div>
              <div style={{ fontSize: 34, fontWeight: 500, color: `var(--${k.tone})`, letterSpacing: "-0.03em", lineHeight: 1, marginTop: 8 }}>{k.v}</div>
              <div className="mono" style={{ fontSize: 11, color: "var(--ink-6)", marginTop: 6 }}>{k.sub}</div>
            </div>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
          <NPSSurveysCard clients={CL}/>
          <MRRTrendCard/>
        </div>
      </div>
    </div>
  );
}

// ── NPS Surveys card ────────────────────────────────────────
function NPSSurveysCard({ clients }) {
  const [list, setList] = React.useState(null);
  const [avg, setAvg]   = React.useState(null);
  const [err, setErr]   = React.useState(null);
  const [modal, setModal] = React.useState(false);
  const [form, setForm] = React.useState({ client_id: "", score: "9", comment: "" });
  const [saving, setSaving] = React.useState(false);
  const [sendModal, setSendModal] = React.useState(false);
  const [sendClientId, setSendClientId] = React.useState("");
  const [sending, setSending] = React.useState(false);

  const NPS_SURVEY_TEXT = "Оцените от 0 до 10 вероятность, что порекомендуете AnyQuery коллегам. Можно добавить комментарий.";

  async function sendSurvey() {
    if (!sendClientId) { appToast("Выберите клиента", "warn"); return; }
    setSending(true);
    try {
      const r = await fetch("/api/nps/send-survey", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: Number(sendClientId) }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || ("HTTP " + r.status));
      }
      const d = await r.json();
      setSendModal(false);
      setSendClientId("");
      appToast(d.note ? `Опрос зафиксирован как отправленный (${d.note})` : "Опрос зафиксирован", "ok");
    } catch (e) { appToast("Ошибка: " + e.message, "error"); }
    setSending(false);
  }

  const reload = React.useCallback(async () => {
    try {
      const r = await fetch("/api/nps", { credentials: "include" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      setList(d.entries || []);
      setAvg(d.avg_nps);
    } catch (e) { setErr(e.message); setList([]); }
  }, []);

  React.useEffect(() => { reload(); }, [reload]);

  async function submit() {
    if (!form.client_id) { appToast("Выберите клиента", "warn"); return; }
    setSaving(true);
    try {
      const r = await fetch("/api/nps", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: Number(form.client_id),
          score: Number(form.score),
          comment: form.comment.trim(),
        }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || ("HTTP " + r.status));
      }
      setModal(false);
      setForm({ client_id: "", score: "9", comment: "" });
      await reload();
      appToast("NPS сохранён", "ok");
    } catch (e) { appToast("Ошибка: " + e.message, "error"); }
    setSaving(false);
  }

  const scoreTone = (s) => s >= 9 ? "ok" : s >= 7 ? "signal" : s >= 5 ? "warn" : "critical";
  const fmtDate = (iso) => { try { return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }); } catch { return "—"; } };

  const header = (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      {avg != null && <Badge tone={avg >= 30 ? "ok" : avg >= 0 ? "warn" : "critical"}>avg {avg}</Badge>}
      <Btn size="s" kind="primary" icon={<I.plus size={12}/>} onClick={() => setModal(true)}>Опрос</Btn>
      <Btn size="s" kind="ghost" onClick={() => setSendModal(true)}>📨 Отправить опрос клиенту</Btn>
    </div>
  );

  return (
    <>
      <Card title="NPS-опросы" action={header}>
        {err && <div style={{ padding: "10px 0", color: "var(--critical)", fontSize: 12.5 }}>Ошибка: {err}</div>}
        {!err && list === null && <div style={{ padding: "20px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>Загрузка…</div>}
        {list && list.length === 0 && (
          <div style={{ padding: "20px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
            Опросов пока нет. Нажмите «Опрос» чтобы добавить первый.
          </div>
        )}
        {list && list.map((e, i) => (
          <div key={e.id} style={{
            display: "grid", gridTemplateColumns: "1fr auto auto",
            gap: 10, alignItems: "center",
            padding: "10px 0",
            borderBottom: i === list.length - 1 ? "none" : "1px solid var(--line-soft)",
          }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 13, color: "var(--ink-9)", fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{e.client_name}</div>
              {e.comment && <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", marginTop: 2 }}>{e.comment}</div>}
            </div>
            <Badge tone={scoreTone(e.score)}>{e.score}</Badge>
            <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>{fmtDate(e.created_at)}</span>
          </div>
        ))}
      </Card>
      {modal && (
        <div onClick={(e) => e.target === e.currentTarget && setModal(false)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 200, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 6, width: 420, padding: 20 }}>
            <div style={{ fontSize: 15, fontWeight: 500, color: "var(--ink-9)", marginBottom: 14 }}>Новый NPS-опрос</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Клиент</span>
                <select value={form.client_id} onChange={(e) => setForm({...form, client_id: e.target.value})}
                  style={{ padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", color: "var(--ink-9)", borderRadius: 4, fontSize: 13 }}>
                  <option value="">— выберите —</option>
                  {clients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </label>
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Score (0..10)</span>
                <input type="number" min="0" max="10" value={form.score}
                  onChange={(e) => setForm({...form, score: e.target.value})}
                  style={{ padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", color: "var(--ink-9)", borderRadius: 4, fontSize: 13 }}/>
              </label>
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Комментарий</span>
                <textarea rows={3} value={form.comment}
                  onChange={(e) => setForm({...form, comment: e.target.value})}
                  style={{ padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", color: "var(--ink-9)", borderRadius: 4, fontSize: 13, fontFamily: "inherit", resize: "vertical" }}/>
              </label>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
              <Btn kind="ghost" size="m" onClick={() => setModal(false)}>Отмена</Btn>
              <Btn kind="primary" size="m" onClick={submit}>{saving ? "…" : "Сохранить"}</Btn>
            </div>
          </div>
        </div>
      )}
      {sendModal && (
        <div onClick={(e) => e.target === e.currentTarget && setSendModal(false)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 200, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 6, width: 460, padding: 20 }}>
            <div style={{ fontSize: 15, fontWeight: 500, color: "var(--ink-9)", marginBottom: 14 }}>Отправить NPS-опрос клиенту</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Клиент</span>
                <select value={sendClientId} onChange={(e) => setSendClientId(e.target.value)}
                  style={{ padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", color: "var(--ink-9)", borderRadius: 4, fontSize: 13 }}>
                  <option value="">— выберите —</option>
                  {clients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </label>
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Текст опроса</span>
                <textarea readOnly value={NPS_SURVEY_TEXT} rows={3}
                  style={{ padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", color: "var(--ink-7)", borderRadius: 4, fontSize: 12.5, fontFamily: "inherit", resize: "vertical", cursor: "not-allowed" }}/>
              </label>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>
                Пока отправка — stub: запись в PartnerLog. Реальная доставка (TG/email) — в разработке.
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
              <Btn kind="ghost" size="m" onClick={() => setSendModal(false)}>Отмена</Btn>
              <Btn kind="primary" size="m" onClick={sendSurvey}>{sending ? "…" : "Отправить"}</Btn>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ── MRR Trend card ────────────────────────────────────────
function MRRTrendCard() {
  const [data, setData] = React.useState(null);
  const [err, setErr]   = React.useState(null);

  React.useEffect(() => {
    let cancelled = false;
    fetch("/api/me/mrr-trend", { credentials: "include" })
      .then(r => r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)))
      .then(d => { if (!cancelled) setData(d); })
      .catch(e => { if (!cancelled) setErr(e.message); });
    return () => { cancelled = true; };
  }, []);

  const fmtMrr = (v) => v >= 1_000_000 ? `₽ ${(v/1_000_000).toFixed(1)}м` : v >= 1_000 ? `₽ ${Math.round(v/1_000)}к` : `₽ ${Math.round(v)}`;

  const header = data && data.nrr != null
    ? <Badge tone={data.nrr >= 100 ? "ok" : data.nrr >= 90 ? "warn" : "critical"}>NRR {data.nrr}%</Badge>
    : null;

  if (err) return <Card title="NRR · MRR тренд">
    <div style={{ padding: "10px 0", color: "var(--critical)", fontSize: 12.5 }}>Ошибка: {err}</div>
  </Card>;

  if (!data) return <Card title="NRR · MRR тренд">
    <div style={{ padding: "20px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>Загрузка…</div>
  </Card>;

  const points = data.points || [];
  const hasData = points.some(p => (p.mrr || 0) > 0);
  const maxV = Math.max(1, ...points.map(p => p.mrr || 0));

  return (
    <Card title="NRR · MRR тренд" action={header}>
      {!hasData && (
        <div style={{ padding: "20px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
          MRR-записей нет. Добавьте RevenueEntry через БД / синк.
        </div>
      )}
      {hasData && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: `repeat(${points.length},1fr)`, gap: 4, alignItems: "end", height: 120, marginTop: 8 }}>
            {points.map((p, i) => {
              const h = Math.max(4, Math.round((p.mrr || 0) / maxV * 100));
              const isLast = i === points.length - 1;
              return (
                <div key={p.period} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
                  <div style={{ width: "100%", height: h, background: isLast ? "var(--signal)" : "var(--ink-4)", borderRadius: "2px 2px 0 0" }} title={fmtMrr(p.mrr || 0)}/>
                  <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)" }}>{p.period.slice(5)}</span>
                </div>
              );
            })}
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 14, padding: "10px 0 0", borderTop: "1px solid var(--line-soft)" }}>
            <div>
              <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Этот месяц</div>
              <div style={{ fontSize: 18, fontWeight: 500, color: "var(--ink-9)", marginTop: 2 }}>{fmtMrr(data.this_mrr || 0)}</div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Прошлый</div>
              <div style={{ fontSize: 18, fontWeight: 500, color: "var(--ink-7)", marginTop: 2 }}>{fmtMrr(data.last_mrr || 0)}</div>
            </div>
          </div>
        </>
      )}
    </Card>
  );
}

// ── Cabinet ───────────────────────────────────────────────
function PageCabinet() {
  const U = (typeof window !== "undefined" && window.__CURRENT_USER) || {};
  const [files, setFiles] = React.useState([]);
  const [uploading, setUploading] = React.useState(false);
  const [recording, setRecording] = React.useState(false);
  const [mediaRec, setMediaRec] = React.useState(null);
  const [reminders, setReminders] = React.useState(
    (typeof window !== "undefined" && window.REMINDERS) || []
  );
  const [newReminder, setNewReminder] = React.useState("");
  const [reminderDate, setReminderDate] = React.useState("");
  const [error, setError] = React.useState("");
  const fileRef = React.useRef(null);

  // Load files on mount
  React.useEffect(() => {
    fetch("/api/files", { credentials: "include" })
      .then(r => r.ok ? r.json() : { files: [] })
      .then(d => setFiles(d.files || []))
      .catch(() => {});
  }, []);

  const uploadFiles = async (fileList) => {
    if (!fileList.length) return;
    setUploading(true); setError("");
    const fd = new FormData();
    for (const f of fileList) fd.append("files", f);
    try {
      const r = await fetch("/api/files", { method: "POST", credentials: "include", body: fd });
      const d = await r.json();
      if (d.ok || d.id || d.filename) {
        // Reload file list
        const lr = await fetch("/api/files", { credentials: "include" });
        const ld = await lr.json();
        setFiles(ld.files || []);
      }
    } catch (e) { setError(String(e)); }
    setUploading(false);
  };

  const deleteFile = async (id) => {
    await fetch("/api/files/" + id, { method: "DELETE", credentials: "include" });
    setFiles(f => f.filter(x => x.id !== id));
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      const chunks = [];
      mr.ondataavailable = e => chunks.push(e.data);
      mr.onstop = async () => {
        const blob = new Blob(chunks, { type: "audio/webm" });
        const fd = new FormData();
        fd.append("files", blob, `voice_${Date.now()}.webm`);
        await fetch("/api/files", { method: "POST", credentials: "include", body: fd });
        stream.getTracks().forEach(t => t.stop());
        const lr = await fetch("/api/files", { credentials: "include" });
        const ld = await lr.json();
        setFiles(ld.files || []);
      };
      mr.start();
      setMediaRec(mr); setRecording(true);
    } catch (e) { setError("Нет доступа к микрофону: " + e.message); }
  };

  const stopRecording = () => {
    if (mediaRec) { mediaRec.stop(); setMediaRec(null); setRecording(false); }
  };

  const addReminder = async () => {
    if (!newReminder.trim() || !reminderDate) return;
    try {
      const r = await fetch("/design/api/reminders", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: newReminder.trim(), remind_at: reminderDate + "T09:00:00" }),
      });
      if (r.ok) {
        setReminders(prev => [...prev, { text: newReminder.trim(), remind_at: reminderDate, done: false }]);
        setNewReminder(""); setReminderDate("");
      }
    } catch (e) { setError(String(e)); }
  };

  const fmtSize = (bytes) => bytes < 1024 ? bytes + " B" : bytes < 1048576 ? Math.round(bytes/1024) + " KB" : (bytes/1048576).toFixed(1) + " MB";
  const fmtDate = (s) => { try { return new Date(s).toLocaleDateString("ru-RU"); } catch { return s; } };

  return (
    <div>
      <TopBar breadcrumbs={["am hub","инструменты","заметки"]} title="Мои заметки"
        subtitle={`${U.name || U.email || "—"} · ${files.length} файлов`}
        actions={<>
          <Btn kind="ghost" size="m" icon={<I.mic size={14}/>} onClick={recording ? stopRecording : startRecording}>
            {recording ? "⏹ Стоп" : "🎙 Запись"}
          </Btn>
          <Btn kind="primary" size="m" icon={<I.plus size={14}/>} onClick={() => fileRef.current?.click()}>
            Загрузить
          </Btn>
        </>}/>
      <input ref={fileRef} type="file" multiple style={{ display: "none" }}
        onChange={e => uploadFiles(Array.from(e.target.files || []))}/>
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 340px", gap: 18 }}>

        {/* Left: Files */}
        <Card title="Файлы и документы" action={uploading && <Badge tone="warn">Загрузка…</Badge>}>
          {error && <div style={{ padding: "8px 10px", background: "color-mix(in oklch, var(--critical) 10%, transparent)", border: "1px solid color-mix(in oklch, var(--critical) 30%, transparent)", borderRadius: 4, color: "var(--critical)", fontSize: 12, marginBottom: 10 }}>{error}</div>}

          {/* Drop zone */}
          <div onDrop={e => { e.preventDefault(); uploadFiles(Array.from(e.dataTransfer.files || [])); }}
            onDragOver={e => e.preventDefault()}
            onClick={() => fileRef.current?.click()}
            style={{ border: "2px dashed var(--line)", borderRadius: 6, padding: "20px 10px", textAlign: "center", cursor: "pointer", marginBottom: 14, color: "var(--ink-6)", fontSize: 13 }}>
            <I.plus size={20} stroke="var(--ink-5)"/>
            <div style={{ marginTop: 6 }}>Перетащите файлы или нажмите для загрузки</div>
            <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", marginTop: 4 }}>PDF, DOCX, XLSX, PNG, WEBM — до 50 МБ</div>
          </div>

          {files.length === 0 ? (
            <div style={{ padding: "20px", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>Файлов пока нет</div>
          ) : (
            files.map((f, i) => (
              <div key={f.id || i} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 0", borderBottom: i === files.length - 1 ? "none" : "1px solid var(--line-soft)" }}>
                <I.doc size={16} stroke="var(--ink-5)"/>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, color: "var(--ink-9)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{f.filename || f.name}</div>
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>
                    {fmtSize(f.size_bytes || f.size || 0)} · {fmtDate(f.created_at)}
                    {f.category && f.category !== "misc" ? " · " + f.category : ""}
                  </div>
                </div>
                <a href={"/api/files/" + f.id} target="_blank" style={{ color: "var(--signal)", textDecoration: "none" }}>
                  <I.download size={13}/>
                </a>
                <button onClick={() => deleteFile(f.id)} style={{ background: "transparent", border: 0, color: "var(--ink-5)", cursor: "pointer", padding: 4 }}>
                  <I.trash size={13}/>
                </button>
              </div>
            ))
          )}
        </Card>

        {/* Right: Reminders + Profile */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <Card title="Напоминания">
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
              <input value={newReminder} onChange={e => setNewReminder(e.target.value)}
                placeholder="Текст напоминания…"
                style={{ padding: "8px 10px", background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-9)", fontFamily: "var(--f-display)", fontSize: 13, outline: "none" }}/>
              <input type="date" value={reminderDate} onChange={e => setReminderDate(e.target.value)}
                style={{ padding: "8px 10px", background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-9)", fontFamily: "var(--f-display)", fontSize: 13, outline: "none" }}/>
              <Btn kind="primary" size="m" full onClick={addReminder} icon={<I.plus size={13}/>}>Добавить</Btn>
            </div>
            {reminders.length === 0 ? (
              <div style={{ fontSize: 12, color: "var(--ink-6)", textAlign: "center", padding: "12px 0" }}>Нет напоминаний</div>
            ) : (
              reminders.slice(0, 8).map((r, i) => (
                <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 8, padding: "8px 0", borderBottom: i === Math.min(reminders.length, 8) - 1 ? "none" : "1px solid var(--line-soft)" }}>
                  <div style={{ width: 6, height: 6, borderRadius: 999, background: r.done ? "var(--ok)" : "var(--warn)", marginTop: 5, flexShrink: 0 }}/>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 12.5, color: r.done ? "var(--ink-6)" : "var(--ink-8)", textDecoration: r.done ? "line-through" : "none" }}>{r.text}</div>
                    {r.remind_at && <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", marginTop: 2 }}>{fmtDate(r.remind_at)}</div>}
                  </div>
                </div>
              ))
            )}
          </Card>

          <Card title="Профиль" dense>
            {[
              { l: "Имя", v: U.name || "—" },
              { l: "Email", v: U.email || "—" },
              { l: "Роль", v: U.role || "—" },
            ].map((r, i) => (
              <div key={i} style={{ padding: "10px 0", borderBottom: i === 2 ? "none" : "1px solid var(--line-soft)", display: "flex", alignItems: "center", gap: 10 }}>
                <span className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", width: 50, flexShrink: 0 }}>{r.l}</span>
                <span style={{ fontSize: 13, color: "var(--ink-8)" }}>{r.v}</span>
              </div>
            ))}
            <Btn kind="ghost" size="m" full style={{ marginTop: 10 }} onClick={() => window.location.href = "/design/profile"}>
              Редактировать профиль
            </Btn>
          </Card>
        </div>
      </div>
    </div>
  );
}

// ── Templates ─────────────────────────────────────────────
function PageTemplates() {
  const tpls = (typeof window !== "undefined" && window.TEMPLATES) || [];
  const [tplModal, setTplModal] = React.useState(false);
  return (
    <div>
      {tplModal && (
        <FormModal title="Новый шаблон"
          fields={[
            { k: "name",     label: "Название",   required: true, placeholder: "Чекап — начало разговора" },
            { k: "category", label: "Категория",  type: "select",
              options: ["general","qbr","sync","checkup","email"].map(function(v){ return {v,l:v}; }) },
            { k: "body",     label: "Текст шаблона ({{name}} — имя клиента)", type: "textarea", required: true,
              placeholder: "Привет {{name}}, давай обсудим…" },
          ]}
          onClose={() => setTplModal(false)}
          onSubmit={async function(vals) {
            const r = await fetch("/design/api/templates", {
              method: "POST", credentials: "include",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ name: vals.name, category: vals.category || "general", body: vals.body }),
            });
            if (!r.ok) throw new Error(await r.text());
            setTplModal(false); location.reload();
          }}
        />
      )}
      <TopBar breadcrumbs={["am hub","шаблоны"]} title="Шаблоны" subtitle="Follow-up, чекапы, QBR — шаблоны общения с клиентами"
        actions={<Btn kind="primary" size="m" icon={<I.plus size={14}/>} onClick={() => setTplModal(true)}>Новый шаблон</Btn>}/>
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
  const [autoModal, setAutoModal] = React.useState(false);
  return (
    <div>
      {autoModal && (
        <FormModal title="Новое правило IF-THEN"
          fields={[
            { k: "name",          label: "Название правила",  required: true, placeholder: "Чекап при падении health" },
            { k: "trigger",       label: "Триггер", type: "select",
              options: [
                { v: "health_drop",       l: "Падение health-score" },
                { v: "days_no_contact",   l: "Нет контакта N дней" },
                { v: "meeting_done",      l: "Встреча завершена" },
                { v: "checkup_due",       l: "Чекап просрочен" },
              ] },
            { k: "task_title",    label: "Задача (THEN — что создать)", required: true, placeholder: "Написать клиенту" },
            { k: "task_priority", label: "Приоритет задачи", type: "select",
              options: [{ v:"low",l:"low"},{v:"medium",l:"medium"},{v:"high",l:"high"}] },
          ]}
          onClose={() => setAutoModal(false)}
          onSubmit={async function(vals) {
            const body = { name: vals.name, trigger: vals.trigger || "health_drop",
              task_title: vals.task_title, task_priority: vals.task_priority || "medium",
              task_due_days: 3, is_active: true, trigger_config: {} };
            if (body.trigger === "health_drop")     body.trigger_config = { threshold: 50 };
            if (body.trigger === "days_no_contact") body.trigger_config = { days: 30 };
            const r = await fetch("/api/auto-tasks/rules", {
              method: "POST", credentials: "include",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            });
            if (!r.ok) throw new Error(await r.text());
            setAutoModal(false); location.reload();
          }}
        />
      )}
      <TopBar breadcrumbs={["am hub","автозадачи"]} title="Автозадачи"
        subtitle="Правила `IF-THEN`: когда система создаёт задачи автоматически"
        actions={<Btn kind="primary" size="m" icon={<I.plus size={14}/>} onClick={() => setAutoModal(true)}>Новое правило</Btn>}/>
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
  const canEdit = !!U.email;
  const [rmModal, setRmModal] = React.useState(null); // null or {col}
  const [dragId, setDragId] = React.useState(null);
  const [dropCol, setDropCol] = React.useState(null);
  const [editId, setEditId] = React.useState(null);
  const [editVal, setEditVal] = React.useState("");
  // items флаттенятся сюда для локального оптимистичного апдейта
  const [localItems, setLocalItems] = React.useState(() => {
    const out = [];
    for (const c of rawCols) {
      for (const it of (c.items || [])) {
        if (typeof it === "string") out.push({ id: null, title: it, column_key: c.key || c.column_key });
        else out.push({ ...it, column_key: it.column_key || c.key || c.column_key });
      }
    }
    return out;
  });

  const DEFAULT = [
    { key: "q1",      title: "Q1 · готово",   tone: "ok" },
    { key: "q2",      title: "Q2 · в работе", tone: "signal" },
    { key: "q3",      title: "Q3 · план",     tone: "info" },
    { key: "q4",      title: "Q4 · идеи",     tone: "warn" },
    { key: "backlog", title: "Бэклог",        tone: "neutral" },
  ];
  const cols = DEFAULT.map(d => ({
    ...d,
    items: localItems.filter(it => (it.column_key || "backlog") === d.key),
  }));

  const addItem = (col) => setRmModal({ col });

  const delItem = async (id) => {
    const r = await fetch(`/design/api/roadmap/${id}`, { method: "DELETE", credentials: "include" });
    if (r.ok) setLocalItems(items => items.filter(i => i.id !== id));
    else if (typeof appToast === "function") appToast("Ошибка удаления", "error");
  };

  const moveItem = async (id, newKey) => {
    // optimistic
    setLocalItems(items => items.map(i => i.id === id ? { ...i, column_key: newKey } : i));
    const r = await fetch(`/design/api/roadmap/${id}`, {
      method: "PATCH", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ column_key: newKey }),
    });
    if (!r.ok) {
      // revert on error — дёрнем reload
      if (typeof appToast === "function") appToast("Ошибка перемещения", "error");
      setTimeout(() => location.reload(), 800);
    }
  };

  // Переставить карточку в порядке колонки: insertBeforeId=null → в конец.
  const reorderItem = async (id, targetKey, insertBeforeId) => {
    // Расcчитываем новый порядок массива items колонки.
    // 1) вынимаем dragged; 2) ставим в позицию по targetKey/insertBeforeId.
    setLocalItems(items => {
      const out = [...items];
      const src = out.findIndex(i => i.id === id);
      if (src < 0) return items;
      const [picked] = out.splice(src, 1);
      picked.column_key = targetKey;
      let insertAt = out.length;
      if (insertBeforeId != null) {
        const idx = out.findIndex(i => i.id === insertBeforeId);
        if (idx >= 0) insertAt = idx;
      }
      out.splice(insertAt, 0, picked);
      // Пересчитываем order_idx в колонке target
      let n = 0;
      for (const it of out) {
        if (it.column_key === targetKey) { it.order_idx = n; n++; }
      }
      return out;
    });
    // Batch PATCH — шлём items текущей колонки
    const batch = [];
    setLocalItems(items => {
      let n = 0;
      for (const it of items) {
        if (it.column_key === targetKey) {
          batch.push({ id: it.id, column_key: targetKey, order_idx: n });
          n++;
        }
      }
      return items;
    });
    try {
      const r = await fetch("/design/api/roadmap/reorder", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: batch }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
    } catch (e) {
      if (typeof appToast === "function") appToast("Не удалось сохранить порядок", "error");
      setTimeout(() => location.reload(), 800);
    }
  };

  const startEdit = (it) => { setEditId(it.id); setEditVal(it.title || ""); };
  const saveEdit = async () => {
    const id = editId; const title = editVal.trim();
    if (!id || !title) { setEditId(null); return; }
    setLocalItems(items => items.map(i => i.id === id ? { ...i, title } : i));
    const r = await fetch(`/design/api/roadmap/${id}`, {
      method: "PATCH", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (!r.ok && typeof appToast === "function") appToast("Не удалось сохранить", "error");
    setEditId(null);
  };

  return (
    <div>
      {rmModal && (
        <FormModal title={`Добавить в «${rmModal.col.title}»`}
          fields={[{ k: "title", label: "Название пункта", required: true, placeholder: "Новая функция…" }]}
          onClose={() => setRmModal(null)}
          onSubmit={async function(vals) {
            const r = await fetch("/design/api/roadmap", {
              method: "POST", credentials: "include",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ column_key: rmModal.col.key, column_title: rmModal.col.title, tone: rmModal.col.tone, title: vals.title }),
            });
            if (!r.ok) throw new Error(await r.text());
            const d = await r.json().catch(() => ({}));
            const newItem = { id: d.id, title: vals.title, column_key: rmModal.col.key };
            setLocalItems(items => [...items, newItem]);
            setRmModal(null);
          }}
        />
      )}
      <TopBar breadcrumbs={["am hub","роадмап"]} title="Роадмап"
        subtitle={canEdit ? `Перетаскивай карточки между кварталами · двойной клик — редактировать` : `Что команда строит в AM Hub · ${new Date().getFullYear()}`}/>
      <div style={{ padding: "22px 28px 40px" }}>
        {!canEdit && localItems.length === 0 && (
          <div style={{ padding: "20px", color: "var(--ink-6)", textAlign: "center", fontSize: 13, background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6, marginBottom: 14 }}>
            Роадмап пока пуст. Пункты добавляют администраторы.
          </div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: `repeat(${cols.length},1fr)`, gap: 14 }}>
          {cols.map((c, i) => (
            <div key={c.key}
              onDragOver={canEdit ? (e) => { e.preventDefault(); setDropCol(c.key); } : undefined}
              onDragLeave={canEdit ? () => setDropCol(null) : undefined}
              onDrop={canEdit ? (e) => {
                e.preventDefault();
                const id = dragId;
                setDragId(null); setDropCol(null);
                if (!id) return;
                // Если дроп прилетел прямо в колонку (не на карточку) — ставим в конец
                reorderItem(id, c.key, null);
              } : undefined}
              style={{
                background: dropCol === c.key ? "color-mix(in oklch, var(--signal) 10%, var(--ink-2))" : "var(--ink-2)",
                border: `1px ${dropCol === c.key ? "dashed var(--signal)" : "solid var(--line)"}`,
                borderTop: `3px solid var(--${c.tone})`,
                borderRadius: "0 0 6px 6px", padding: 14,
                minHeight: 140, transition: "background .12s, border-color .12s",
              }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <div className="mono" style={{ fontSize: 11, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.1em" }}>{c.title} · {c.items.length}</div>
                {canEdit && (
                  <button onClick={() => addItem(c)} title="Добавить"
                    style={{ background: "transparent", border: "1px solid var(--line)", color: "var(--ink-7)", width: 22, height: 22, borderRadius: 3, cursor: "pointer", fontSize: 14, lineHeight: 1, padding: 0 }}>+</button>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {c.items.map((it, j) => {
                  const dragProps = (canEdit && it.id) ? {
                    draggable: true,
                    onDragStart: (e) => { setDragId(it.id); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(it.id)); },
                    onDragEnd: () => { setDragId(null); setDropCol(null); },
                    onDragOver: (e) => {
                      if (!dragId || dragId === it.id) return;
                      e.preventDefault(); e.stopPropagation();
                      setDropCol(c.key);
                    },
                    onDrop: (e) => {
                      if (!dragId) return;
                      e.preventDefault(); e.stopPropagation();
                      const id = dragId;
                      setDragId(null); setDropCol(null);
                      if (id === it.id) return;
                      // Определяем insert before/after по Y курсора относительно центра
                      const box = e.currentTarget.getBoundingClientRect();
                      const isAbove = (e.clientY - box.top) < (box.height / 2);
                      // Если above → вставляем перед it.id; иначе — перед следующим (или null = конец)
                      let insertBefore = it.id;
                      if (!isAbove) {
                        const idx = c.items.findIndex(x => x.id === it.id);
                        const nxt = c.items[idx + 1];
                        insertBefore = nxt ? nxt.id : null;
                      }
                      reorderItem(id, c.key, insertBefore);
                    },
                  } : {};
                  const isDragging = dragId === it.id;
                  return (
                  <div key={it.id || j}
                    {...dragProps}
                    onDoubleClick={canEdit && it.id ? () => startEdit(it) : undefined}
                    style={{
                      padding: 10,
                      background: isDragging ? "color-mix(in oklch, var(--signal) 12%, var(--ink-1))" : "var(--ink-1)",
                      border: `1px solid ${isDragging ? "var(--signal)" : "var(--line-soft)"}`,
                      borderRadius: 4,
                      display: "flex", alignItems: "center", gap: 8,
                      cursor: canEdit && it.id ? "grab" : "default",
                      opacity: isDragging ? 0.6 : 1,
                    }}>
                    {canEdit && it.id && (
                      <span title="Перетащить" style={{ color: "var(--ink-5)", fontSize: 12, userSelect: "none", cursor: "grab" }}>⋮⋮</span>
                    )}
                    {editId === it.id ? (
                      <input
                        autoFocus value={editVal}
                        onChange={e => setEditVal(e.target.value)}
                        onBlur={saveEdit}
                        onKeyDown={e => { if (e.key === "Enter") saveEdit(); else if (e.key === "Escape") setEditId(null); }}
                        style={{ flex: 1, padding: "4px 6px", background: "var(--ink-2)", border: "1px solid var(--signal)", borderRadius: 3, color: "var(--ink-9)", fontSize: 12.5, outline: "none" }}/>
                    ) : (
                      <div style={{ flex: 1, fontSize: 12.5, color: "var(--ink-8)" }}
                        title={canEdit ? "Двойной клик — редактировать" : ""}>
                        {it.title || ""}
                      </div>
                    )}
                    {canEdit && it.id && editId !== it.id && (
                      <button onClick={() => delItem(it.id)} title="Удалить"
                        style={{ background: "transparent", border: 0, color: "var(--ink-5)", cursor: "pointer", padding: 2, fontSize: 12 }}>✕</button>
                    )}
                  </div>
                  );
                })}
                {c.items.length === 0 && (
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", fontStyle: "italic", padding: "14px 0", textAlign: "center" }}>
                    {dropCol === c.key ? "отпусти сюда" : "пусто"}
                  </div>
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
  const [intModal, setIntModal] = React.useState(false);
  return (
    <div>
      {intModal && (
        <FormModal title="Новая внутренняя задача"
          fields={[
            { k: "title",    label: "Задача",    required: true, placeholder: "Обновить регламент" },
            { k: "priority", label: "Приоритет", type: "select",
              options: [{v:"low",l:"low"},{v:"med",l:"med"},{v:"high",l:"high"}], default: "med" },
            { k: "due",      label: "Срок (дней от сегодня)", type: "number", default: "7", placeholder: "7" },
          ]}
          onClose={() => setIntModal(false)}
          onSubmit={async function(vals) {
            const r = await fetch("/design/api/internal-tasks", {
              method: "POST", credentials: "include",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ title: vals.title, priority: vals.priority || "med", due: vals.due || "7" }),
            });
            if (!r.ok) throw new Error(await r.text());
            setIntModal(false); location.reload();
          }}
        />
      )}
      <TopBar breadcrumbs={["am hub","внутренние задачи"]} title="Внутренние задачи"
        subtitle="Задачи команды без привязки к клиенту"
        actions={<Btn kind="primary" size="m" icon={<I.plus size={14}/>} onClick={() => setIntModal(true)}>Задача</Btn>}/>
      <div style={{ padding: "22px 28px 40px" }}>
        <Card title="Задачи команды">
          {(function(){
            // Предпочитаем window.TASKS (отфильтрован сервером: client_id IS NULL
            // либо source='internal') — единый источник правды. Fallback — INTERNAL_TASKS
            // для обратной совместимости.
            const srvTasks = (typeof window !== "undefined" && Array.isArray(window.TASKS)) ? window.TASKS : [];
            const intTasks = (typeof window !== "undefined" && Array.isArray(window.INTERNAL_TASKS)) ? window.INTERNAL_TASKS : [];
            const items = srvTasks.length ? srvTasks : intTasks;
            if (!items.length) {
              return <div style={{ padding: "30px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
                Внутренних задач пока нет.
              </div>;
            }
            return items.map((r,i,a)=>{
              const pr = r.priority || r.pr || "low";
              // window.TASKS не содержит owner — в task_to_design есть только client.
              const owner = r.owner || r.team || (r.client && r.client !== "—" ? r.client : "—");
              return (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "20px 1fr 180px 80px 80px", gap: 14, padding: "12px 6px", borderBottom: i===a.length-1?"none":"1px solid var(--line-soft)", alignItems: "center" }}>
                  <TaskCheck checked={r.status === "done" || !!r.done}
                    onChange={async (checked) => {
                      if (!r.id) return;
                      const newStatus = checked ? "done" : "plan";
                      try {
                        const resp = await fetch(`/api/tasks/${r.id}/status`, {
                          method: "PATCH", credentials: "include",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ status: newStatus }),
                        });
                        if (!resp.ok) throw new Error("HTTP " + resp.status);
                        appToast(newStatus === "done" ? "Задача закрыта" : "Задача открыта", "ok");
                        location.reload();
                      } catch (err) { appToast("Ошибка: " + err.message, "error"); }
                    }}/>
                  <span style={{ fontSize: 13, color: (r.status === "done" || r.done) ? "var(--ink-5)" : "var(--ink-8)", textDecoration: (r.status === "done" || r.done) ? "line-through" : "none" }}>{r.title || r.t}</span>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}><Avatar name={owner} size={20}/><span style={{ fontSize: 12, color: "var(--ink-7)" }}>{owner}</span></div>
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

  // API tokens state
  const [tokens, setTokens] = React.useState([]);
  const [tokensLoading, setTokensLoading] = React.useState(true);
  const [tokensErr, setTokensErr] = React.useState(null);
  const [newName, setNewName] = React.useState("");
  const [newToken, setNewToken] = React.useState(null);  // показываем сырой токен после create
  const [creating, setCreating] = React.useState(false);

  // Integrations status (ktalk / tbank_time / merchrules / airtable / telegram)
  const [integrations, setIntegrations] = React.useState(null);

  async function reloadTokens() {
    setTokensLoading(true);
    setTokensErr(null);
    try {
      const r = await fetch("/api/me/api-tokens", { credentials: "include" });
      if (r.status === 401) { setTokensErr("Нужна авторизация"); setTokens([]); return; }
      if (!r.ok) { setTokensErr("Ошибка " + r.status); setTokens([]); return; }
      const d = await r.json();
      setTokens(d.tokens || []);
    } catch (e) {
      setTokensErr(String(e.message || e));
    } finally {
      setTokensLoading(false);
    }
  }

  async function reloadIntegrations() {
    try {
      const r = await fetch("/api/me/integrations", { credentials: "include" });
      if (!r.ok) { setIntegrations({}); return; }
      const d = await r.json();
      setIntegrations(d || {});
    } catch (e) {
      setIntegrations({});
    }
  }

  React.useEffect(() => { reloadTokens(); reloadIntegrations(); }, []);

  async function createToken() {
    const name = (newName || "").trim();
    if (!name) return;
    setCreating(true);
    try {
      const r = await fetch("/api/me/api-tokens", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ name }),
      });
      if (!r.ok) { appToast("Ошибка: " + r.status); return; }
      const d = await r.json();
      setNewToken(d.token);
      setNewName("");
      reloadTokens();
    } finally {
      setCreating(false);
    }
  }

  async function revokeToken(id) {
    if (!await appConfirm("Отозвать токен? Устройства с ним перестанут работать.")) return;
    const r = await fetch("/api/me/api-tokens/" + encodeURIComponent(id), {
      method: "DELETE", credentials: "include",
    });
    if (r.ok) reloadTokens();
    else appToast("Ошибка отзыва: " + r.status);
  }

  // Пересоздать: удалить старый токен и сразу создать новый с тем же именем.
  // Полный токен покажется в карточке new-token-display как при обычном create.
  async function regenerateToken(id, name) {
    if (!await appConfirm(
      `Пересоздать токен «${name}»?\n\nСтарый будет отозван — устройства с ним ` +
      `перестанут работать. Появится новый полный токен, который нужно будет ` +
      `скопировать и вставить в расширение ещё раз.`
    )) return;
    // 1) revoke old
    const delResp = await fetch("/api/me/api-tokens/" + encodeURIComponent(id), {
      method: "DELETE", credentials: "include",
    });
    if (!delResp.ok) { appToast("Не удалось отозвать старый: " + delResp.status); return; }
    // 2) create new with same name
    const createResp = await fetch("/api/me/api-tokens", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ name: name || "Пересоздан" }),
    });
    if (!createResp.ok) { appToast("Старый отозван, но новый не создался: " + createResp.status); reloadTokens(); return; }
    const d = await createResp.json();
    setNewToken(d.token);
    reloadTokens();
  }

  // Копирование префикса токена (полный уже не восстановить — только хэш в БД).
  async function copyPrefix(prefix, name) {
    try {
      await navigator.clipboard.writeText(prefix);
      appToast(
        `Скопировал префикс: ${prefix}…\n\n` +
        `ℹ️ Это только начало токена — полный мы не храним (только hash).\n` +
        `Если потерян полный токен для «${name}», нажми 🔄 чтобы пересоздать.`
      );
    } catch (e) {
      appToast("Не удалось скопировать: " + e.message);
    }
  }

  // Простой clipboard helper с fallback
  function _copy(text, label) {
    try {
      navigator.clipboard.writeText(text);
    } catch (e) { /* clipboard unavailable, silently skip */ }
  }

  return (
    <div>
      <TopBar breadcrumbs={["am hub","расширение"]} title="Расширения браузера"
        subtitle={`${EXTS.length} ${EXTS.length === 1 ? "расширение" : "расширения"} · скачать, установить, настроить`}/>
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 380px", gap: 28 }}>

        {/* ── LEFT: карточки расширений ───────────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>

          {/* Интеграции — статусы и кнопки входа в внешние системы */}
          <Card title="Интеграции" action={<span className="mono" style={{ fontSize: 10, color: "var(--ink-5)" }}>личные токены</span>}>
            {(function() {
              const ints = integrations || {};
              const rows = [
                {
                  key: "ktalk",
                  name: "Ktalk",
                  desc: "Встречи · tbank.ktalk.ru · токен через расширение AM Hub",
                  connected: !!ints.ktalk,
                  action: { label: "Открыть Ktalk", kind: "primary",
                    onClick: function() { window.open("https://tbank.ktalk.ru/", "_blank"); } },
                  actionReconnect: { label: "Открыть Ktalk", kind: "ghost",
                    onClick: function() { window.open("https://tbank.ktalk.ru/", "_blank"); } },
                },
                {
                  key: "tbank_time",
                  name: "Tbank Time",
                  desc: "Тайм-трекинг · time.tbank.ru · расширение захватит токен",
                  connected: !!ints.tbank_time,
                  action: { label: "Открыть Time", kind: "primary",
                    onClick: function() {
                      window.open("https://time.tbank.ru", "_blank");
                      if (typeof appToast === "function") appToast("Войди в Time — расширение захватит токен автоматически", "ok");
                    } },
                  actionReconnect: { label: "Обновить токен", kind: "ghost",
                    onClick: function() {
                      window.open("https://time.tbank.ru", "_blank");
                      if (typeof appToast === "function") appToast("Войди в Time — расширение захватит новый токен", "ok");
                    } },
                },
                {
                  key: "merchrules",
                  name: "Merchrules",
                  desc: "Синхронизация клиентов · логин/пароль в popup расширения",
                  connected: !!ints.merchrules,
                  action: { label: "Настроить", kind: "ghost",
                    onClick: function() { window.location.href = "/design/integrations"; } },
                  actionReconnect: { label: "Изменить", kind: "ghost",
                    onClick: function() { window.location.href = "/design/integrations"; } },
                },
                {
                  key: "airtable",
                  name: "Airtable",
                  desc: "Импорт портфеля · Personal Access Token",
                  connected: !!ints.airtable,
                  action: { label: "Настроить", kind: "ghost",
                    onClick: function() { window.location.href = "/design/integrations"; } },
                  actionReconnect: { label: "Изменить", kind: "ghost",
                    onClick: function() { window.location.href = "/design/integrations"; } },
                },
                {
                  key: "telegram",
                  name: "Telegram",
                  desc: "Уведомления и voice-заметки · через @am_hub_bot",
                  connected: !!ints.telegram,
                  action: { label: "Привязать", kind: "ghost",
                    onClick: function() { window.location.href = "/design/profile"; } },
                  actionReconnect: { label: "Изменить", kind: "ghost",
                    onClick: function() { window.location.href = "/design/profile"; } },
                },
              ];
              return rows.map(function(r, i, a) {
                const act = r.connected ? r.actionReconnect : r.action;
                return (
                  <div key={r.key} style={{
                    display: "grid",
                    gridTemplateColumns: "140px 1fr auto",
                    gap: 14, alignItems: "center",
                    padding: "12px 0",
                    borderBottom: i === a.length - 1 ? "none" : "1px solid var(--line-soft)"
                  }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-9)" }}>{r.name}</span>
                      {integrations === null
                        ? <Badge tone="neutral">…</Badge>
                        : r.connected
                          ? <Badge tone="ok" dot>подключено</Badge>
                          : <Badge tone="neutral" dot>нет</Badge>}
                    </div>
                    <div style={{ fontSize: 12, color: "var(--ink-6)", lineHeight: 1.4 }}>{r.desc}</div>
                    <Btn size="s" kind={act.kind} onClick={act.onClick}>{act.label}</Btn>
                  </div>
                );
              });
            })()}
          </Card>

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
                <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>AM Hub · токен (постоянный, отзывный)</div>

                {/* Список существующих токенов */}
                <div style={{ marginBottom: 8 }}>
                  {tokensLoading && (
                    <div style={{ fontSize: 11, color: "var(--ink-5)", padding: "4px 0" }}>Загрузка…</div>
                  )}
                  {tokensErr && (
                    <div style={{ fontSize: 11, color: "var(--err, #f0556a)", padding: "4px 0" }}>{tokensErr}</div>
                  )}
                  {!tokensLoading && !tokensErr && tokens.length === 0 && (
                    <div style={{ fontSize: 11, color: "var(--ink-5)", padding: "4px 0" }}>Нет активных токенов — создай первый ↓</div>
                  )}
                  {tokens.map(t => (
                    <div key={t.id} style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 8px", background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 4, marginBottom: 4 }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-9)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{t.name}</div>
                        <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)" }}>
                          {t.prefix}… {t.last_used_at ? "· использовался " + new Date(t.last_used_at).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : "· не использовался"}
                        </div>
                      </div>
                      <button onClick={() => copyPrefix(t.prefix, t.name)} title="Скопировать префикс токена"
                        style={{ background: "transparent", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-6)", cursor: "pointer", padding: "3px 8px", fontSize: 11 }}>📋</button>
                      <button onClick={() => regenerateToken(t.id, t.name)} title="Пересоздать — отзовёт старый и покажет новый полный токен"
                        style={{ background: "transparent", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-6)", cursor: "pointer", padding: "3px 8px", fontSize: 11 }}>🔄</button>
                      <button onClick={() => revokeToken(t.id)} title="Отозвать"
                        style={{ background: "transparent", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-6)", cursor: "pointer", padding: "3px 8px", fontSize: 11 }}>✕</button>
                    </div>
                  ))}
                </div>

                {/* Форма создания */}
                <div style={{ display: "flex", gap: 6 }}>
                  <input
                    value={newName}
                    onChange={e => setNewName(e.target.value)}
                    placeholder="Название (например: Chrome на ноуте)"
                    onKeyDown={e => { if (e.key === "Enter") createToken(); }}
                    style={{ flex: 1, padding: "7px 10px", background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-9)", fontSize: 12, outline: "none" }}/>
                  <Btn size="s" kind="primary" onClick={createToken} disabled={creating || !newName.trim()}>
                    {creating ? "…" : "+ Создать"}
                  </Btn>
                </div>

                {/* Показ свежесозданного токена (раз и навсегда) */}
                {newToken && (
                  <div style={{ marginTop: 10, padding: 10, background: "rgba(163,230,53,.10)", border: "1px solid rgba(163,230,53,.35)", borderRadius: 4 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: "#a3e635", marginBottom: 6 }}>⚠️ Сохрани токен — больше не покажем</div>
                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <code className="mono" style={{ flex: 1, fontSize: 11, color: "var(--ink-9)", wordBreak: "break-all" }}>{newToken}</code>
                      <Btn size="s" kind="ghost" icon={<I.copy size={12}/>} onClick={() => _copy(newToken, "API token")}>копия</Btn>
                      <button onClick={() => setNewToken(null)}
                        style={{ background: "transparent", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-6)", cursor: "pointer", padding: "3px 8px", fontSize: 11 }}>✕</button>
                    </div>
                    <div style={{ fontSize: 10, color: "var(--ink-6)", marginTop: 6, lineHeight: 1.4 }}>
                      Вставь в поле <b>AM HUB · TOKEN</b> расширения. Можно отозвать — устройства с этим токеном перестанут работать.
                    </div>
                  </div>
                )}
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

// ── Profile ───────────────────────────────────────────────
function PageProfile() {
  const [prof, setProf] = React.useState(null);
  const [saving, setSaving] = React.useState(false);
  const [msg, setMsg] = React.useState(null);

  React.useEffect(() => {
    fetch("/design/api/profile", { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => setProf(d));
  }, []);

  const save = async (e) => {
    e.preventDefault();
    setSaving(true); setMsg(null);
    const body = {
      email: prof.email || "",
      first_name: prof.first_name || "",
      last_name: prof.last_name || "",
      telegram_id: prof.telegram_id || "",
    };
    const r = await fetch("/design/api/profile", {
      method: "PUT", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setSaving(false);
    if (r.ok) {
      // Перечитываем профиль — чтобы обновились «Клиентов (по email)»,
      // «Клиентов (assigned)» и другая статистика справа.
      try {
        const fresh = await fetch("/design/api/profile", { credentials: "include" }).then(x => x.ok ? x.json() : null);
        if (fresh) setProf(fresh);
      } catch (e) { /* non-fatal */ }
      setMsg("Сохранено. Если клиентов (по email) всё ещё 0 — запусти ⟲ Из Airtable на /design/portfolio.");
    } else {
      const d = await r.json().catch(() => ({}));
      setMsg(d.detail || d.error || "Ошибка сохранения");
    }
    setTimeout(() => setMsg(null), 6000);
  };

  if (!prof) return <div style={{ padding: 40, color: "var(--ink-6)", textAlign: "center" }}>Загружаю профиль…</div>;

  return (
    <div>
      <TopBar breadcrumbs={["am hub","профиль"]} title="Мой профиль" subtitle={prof.email}/>
      <div style={{ padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "1fr 320px", gap: 18 }}>
        <Card title="Личные данные">
          <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {[
              { k: "email",       l: "Email",       readonly: false, type: "email",
                hint: "Должен совпадать с CSM-email в Airtable, иначе клиенты не подтянутся" },
              { k: "first_name",  l: "Имя / инициалы", type: "text" },
              { k: "last_name",   l: "Фамилия",     type: "text" },
              { k: "telegram_id", l: "Telegram ID (chat_id)", type: "text" },
            ].map(f => (
              <label key={f.k} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{f.l}</span>
                <input type={f.type} value={prof[f.k] || ""} readOnly={f.readonly} disabled={f.readonly}
                  onChange={(e) => setProf({ ...prof, [f.k]: e.target.value })}
                  style={{ padding: "8px 10px", background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-8)", fontSize: 13, fontFamily: "var(--f-mono)", outline: "none" }}/>
              </label>
            ))}
            <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 6 }}>
              <Btn kind="primary" type="submit" size="m" disabled={saving}>{saving ? "Сохраняю…" : "Сохранить"}</Btn>
              {msg && <span className="mono" style={{ fontSize: 11, color: msg.includes("Ошибка") ? "var(--critical)" : "var(--ok)" }}>{msg}</span>}
            </div>
          </form>
        </Card>

        <Card title="Статистика">
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {[
              { l: "Роль",                v: prof.role },
              { l: "Клиентов (по email)", v: prof.clients_by_email },
              { l: "Клиентов (assigned)", v: prof.clients_assigned },
              { l: "Telegram",            v: prof.telegram_id ? "✓" : "не привязан" },
            ].map((r, i) => {
              // Валидные значения: строки, включая пустые; числа включая 0;
              // только null/undefined показываем как "—".
              const shown = r.v == null || r.v === "" ? "—" : String(r.v);
              return (
                <div key={i} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid var(--line-soft)" }}>
                  <span className="mono" style={{ fontSize: 11, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{r.l}</span>
                  <span style={{ fontSize: 13, color: "var(--ink-9)", fontWeight: 500 }}>{shown}</span>
                </div>
              );
            })}
          </div>
        </Card>
      </div>
    </div>
  );
}

// ── Assignments admin ─────────────────────────────────────
function PageAssignments() {
  const U = (typeof window !== "undefined" && window.__CURRENT_USER) || {};
  const CL = (typeof window !== "undefined" && window.CLIENTS) || [];
  const [managers, setManagers] = React.useState([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    fetch("/design/api/users", { credentials: "include" })
      .then(r => r.ok ? r.json() : { users: [] })
      .then(d => { setManagers(d.users || d || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  if ((U.role || "") !== "admin") {
    return (
      <div>
        <TopBar breadcrumbs={["am hub","админ","назначения"]} title="Назначения клиентов"/>
        <div style={{ padding: "40px 28px", color: "var(--ink-6)", textAlign: "center" }}>
          Доступ только для администраторов.
        </div>
      </div>
    );
  }

  const [assignModal, setAssignModal] = React.useState(null); // null or {id, name}

  const reassign = (clientId, clientName) => setAssignModal({ id: clientId, name: clientName });

  return (
    <div>
      {assignModal && (
        <FormModal title={`Передать «${assignModal.name}»`}
          fields={[
            { k: "email", label: "Email нового менеджера", required: true, type: "email",
              placeholder: "manager@company.ru" },
          ]}
          onClose={() => setAssignModal(null)}
          onSubmit={async function(vals) {
            const r = await fetch("/design/api/assign-client", {
              method: "POST", credentials: "include",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ client_id: assignModal.id, manager_email: vals.email.trim().toLowerCase() }),
            });
            if (!r.ok) throw new Error(await r.text());
            setAssignModal(null); location.reload();
          }}
          submitLabel="Передать"
        />
      )}
      <TopBar breadcrumbs={["am hub","админ","назначения"]} title="Назначения клиентов"
        subtitle={`${CL.length} клиентов · ${managers.length || "?"} менеджеров`}/>
      <div style={{ padding: "22px 28px 40px" }}>
        <Card title="Передать клиента">
          {CL.length === 0 && (
            <div style={{ padding: "20px 0", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
              Нет клиентов в системе.
            </div>
          )}
          <div style={{ background: "var(--ink-2)", borderRadius: 4 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 100px 120px", gap: 14, padding: "10px 14px", background: "var(--ink-1)", fontFamily: "var(--f-mono)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--ink-5)" }}>
              <span>клиент</span>
              <span>текущий менеджер</span>
              <span>сегмент</span>
              <span>действие</span>
            </div>
            {CL.map((c, i) => (
              <div key={c.id} style={{ display: "grid", gridTemplateColumns: "1fr 1fr 100px 120px", gap: 14, padding: "12px 14px", borderBottom: i === CL.length - 1 ? "none" : "1px solid var(--line-soft)", alignItems: "center" }}>
                <span style={{ fontSize: 13, color: "var(--ink-9)" }}>{c.name}</span>
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-6)" }}>{c.manager_email || c.pm || "—"}</span>
                <Seg value={c.segment || c.seg}/>
                <Btn size="s" kind="ghost" onClick={() => reassign(c.id, c.name)}>Передать</Btn>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

// ── QBR Calendar ──────────────────────────────────────────
function PageQBR() {
  const QBR_DATA = (typeof window !== "undefined" && window.QBR_DATA) || [];
  const CL_QBR   = (typeof window !== "undefined" && window.CLIENTS) || [];
  const [syncing, setSyncing] = React.useState(false);
  const [syncMsg, setSyncMsg] = React.useState(null);
  const [editQbr, setEditQbr] = React.useState(null);
  const [saving, setSaving] = React.useState(false);
  const [qbrCreateModal, setQbrCreateModal] = React.useState(false);

  // Build month columns: last 3 months + next 3 months from today
  const today = new Date();
  const months = [];
  for (let i = -3; i <= 3; i++) {
    const d = new Date(today.getFullYear(), today.getMonth() + i, 1);
    months.push({
      key: d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"),
      label: d.toLocaleString("ru-RU", { month: "short", year: "2-digit" }),
      year: d.getFullYear(),
      month: d.getMonth() + 1,
    });
  }
  const todayYM = today.toISOString().slice(0, 7);

  // Build rows grouped by manager
  const mgrMap = {};
  QBR_DATA.forEach(function(q) {
    const mgr = q.manager_email || "—";
    if (!mgrMap[mgr]) mgrMap[mgr] = {};
    const clKey = q.client_name + ":" + q.client_id;
    if (!mgrMap[mgr][clKey]) mgrMap[mgr][clKey] = { client_name: q.client_name, client_id: q.client_id, cells: {} };
    if (q.date) mgrMap[mgr][clKey].cells[q.date.slice(0, 7)] = q;
  });
  const rows = Object.keys(mgrMap).map(function(mgr) {
    return { manager_email: mgr, clients: Object.values(mgrMap[mgr]) };
  });

  const isOverdue = function(dateStr, status) {
    if (!dateStr || status === "completed" || status === "cancelled") return false;
    return new Date(dateStr) < today;
  };
  const statusColor = function(status, dateStr) {
    if (isOverdue(dateStr, status)) return "var(--critical)";
    if (status === "completed") return "var(--ok)";
    if (status === "scheduled") return "var(--signal)";
    return "var(--ink-5)";
  };

  const totalQbrs = QBR_DATA.length;
  const completed = QBR_DATA.filter(function(q){ return q.status === "completed"; }).length;
  const scheduled = QBR_DATA.filter(function(q){ return q.status === "scheduled"; }).length;
  const overdue = QBR_DATA.filter(function(q){ return isOverdue(q.date, q.status); }).length;

  const syncAirtable = async function() {
    setSyncing(true); setSyncMsg(null);
    try {
      const r = await fetch("/design/api/qbr/sync-airtable", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
      });
      const d = await r.json();
      setSyncMsg(d.ok
        ? "Синхронизировано: +" + d.created + " создано, " + d.updated + " обновлено"
        : "Ошибка: " + (d.error || "неизвестная"));
      if (d.ok) setTimeout(function(){ location.reload(); }, 1200);
    } catch (e) {
      setSyncMsg("Ошибка: " + e.message);
    } finally {
      setSyncing(false);
    }
  };

  const saveQbr = async function() {
    if (!editQbr) return;
    setSaving(true);
    try {
      const r = await fetch("/design/api/qbr", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: editQbr.client_id,
          quarter: editQbr.quarter,
          date: editQbr.date,
          status: editQbr.status || "scheduled",
        }),
      });
      const d = await r.json();
      if (d.ok) { setEditQbr(null); location.reload(); }
      else appToast("Ошибка: " + (d.detail || JSON.stringify(d)));
    } catch (e) {
      appToast("Ошибка: " + e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      {qbrCreateModal && (
        <FormModal title="Запланировать QBR"
          fields={[
            { k: "client_id", label: "Клиент", required: true, type: "select",
              options: [{ v:"", l:"— выберите клиента —" }].concat(CL_QBR.map(function(c){ return { v: String(c.id), l: c.name }; })) },
            { k: "quarter", label: "Квартал (напр. 2026-Q2)", required: true,
              default: today.getFullYear() + "-Q" + Math.ceil((today.getMonth()+1)/3),
              placeholder: "2026-Q2" },
            { k: "date", label: "Дата QBR", required: true, type: "date",
              default: today.toISOString().slice(0,10) },
            { k: "status", label: "Статус", type: "select",
              options: [{v:"scheduled",l:"Запланирован"},{v:"completed",l:"Проведён"},{v:"cancelled",l:"Отменён"}] },
          ]}
          onClose={() => setQbrCreateModal(false)}
          onSubmit={async function(vals) {
            if (!vals.client_id) throw new Error("Выберите клиента");
            setQbrCreateModal(false);
            setEditQbr({ client_id: parseInt(vals.client_id,10),
              client_name: (CL_QBR.find(function(c){ return String(c.id) === vals.client_id; }) || {}).name || ("Клиент #"+vals.client_id),
              quarter: vals.quarter, date: vals.date, status: vals.status || "scheduled" });
          }}
          submitLabel="Далее →"
        />
      )}
      <TopBar
        breadcrumbs={["am hub", "qbr"]}
        title="QBR Календарь"
        subtitle={totalQbrs + " записей · " + completed + " проведено · " + scheduled + " запланировано · " + overdue + " просрочено"}
        actions={<>
          <Btn kind="ghost" size="m" icon={<I.plus size={14}/>} onClick={() => setQbrCreateModal(true)}>Запланировать QBR</Btn>
          <Btn kind="primary" size="m" disabled={syncing} onClick={syncAirtable}>
            {syncing ? "Синхронизация…" : "Синхронизировать с Airtable"}
          </Btn>
        </>}
      />

      {syncMsg && (
        <div style={{
          margin: "0 28px 14px", padding: "10px 14px", borderRadius: 6, fontSize: 12,
          background: syncMsg.startsWith("Ошибка") ? "color-mix(in oklch, var(--critical) 12%, transparent)" : "color-mix(in oklch, var(--ok) 12%, transparent)",
          border: "1px solid " + (syncMsg.startsWith("Ошибка") ? "color-mix(in oklch, var(--critical) 30%, transparent)" : "color-mix(in oklch, var(--ok) 30%, transparent)"),
          color: syncMsg.startsWith("Ошибка") ? "var(--critical)" : "var(--ok)",
        }}>{syncMsg}</div>
      )}

      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12 }}>
          <KPI label="Всего QBR" value={totalQbrs} sub="записей"/>
          <KPI label="Проведено" value={completed} tone="ok"/>
          <KPI label="Запланировано" value={scheduled} tone="signal"/>
          <KPI label="Просрочено" value={overdue} tone={overdue > 0 ? "critical" : undefined}/>
        </div>

        {QBR_DATA.length === 0 ? (
          <div style={{ padding: "40px 20px", color: "var(--ink-6)", textAlign: "center", fontSize: 13, background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6 }}>
            Нет данных QBR. Нажмите «Синхронизировать с Airtable» для импорта.
          </div>
        ) : (
          <Card title="QBR Календарь · по менеджерам">
            <div style={{ overflowX: "auto" }}>
              {/* Header */}
              <div style={{ display: "grid", gridTemplateColumns: "200px " + months.map(function(){ return "1fr"; }).join(" "), gap: 0,
                background: "var(--ink-1)", borderBottom: "1px solid var(--line)", padding: "8px 0",
                fontFamily: "var(--f-mono)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em",
                color: "var(--ink-5)", minWidth: 600 }}>
                <span style={{ padding: "0 10px" }}>клиент</span>
                {months.map(function(m) {
                  return (
                    <span key={m.key} style={{ padding: "0 6px", textAlign: "center", color: m.key === todayYM ? "var(--signal)" : undefined }}>
                      {m.label}
                    </span>
                  );
                })}
              </div>

              {rows.map(function(mgrRow, mi) {
                return (
                  <div key={mgrRow.manager_email}>
                    <div style={{ padding: "8px 10px", background: "var(--ink-2)",
                      borderTop: mi > 0 ? "2px solid var(--line)" : undefined,
                      borderBottom: "1px solid var(--line-soft)",
                      display: "flex", alignItems: "center", gap: 8 }}>
                      <Avatar name={mgrRow.manager_email} size={20}/>
                      <span style={{ fontSize: 12, fontWeight: 500, color: "var(--ink-8)" }}>{mgrRow.manager_email}</span>
                      <Badge tone="info">{mgrRow.clients.length} клиентов</Badge>
                    </div>

                    {mgrRow.clients.map(function(cl, ci) {
                      return (
                        <div key={cl.client_name + cl.client_id}
                          style={{ display: "grid", gridTemplateColumns: "200px " + months.map(function(){ return "1fr"; }).join(" "), gap: 0,
                            borderBottom: ci === mgrRow.clients.length - 1 ? "none" : "1px solid var(--line-soft)",
                            alignItems: "center", minWidth: 600 }}>
                          <span style={{ padding: "10px 10px", fontSize: 12.5, color: "var(--ink-8)", fontWeight: 500,
                            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {cl.client_name}
                          </span>
                          {months.map(function(m) {
                            const q = cl.cells[m.key];
                            if (!q) {
                              return (
                                <div key={m.key} style={{ padding: "8px 6px", display: "flex", justifyContent: "center" }}>
                                  <button
                                    onClick={() => setEditQbr({ client_id: cl.client_id, client_name: cl.client_name,
                                      quarter: m.year + "-Q" + Math.ceil(m.month / 3),
                                      date: m.key + "-01", status: "scheduled" })}
                                    style={{ width: 24, height: 24, border: "1px dashed var(--line)", borderRadius: 4,
                                      background: "transparent", cursor: "pointer", color: "var(--ink-5)", fontSize: 14 }}>+</button>
                                </div>
                              );
                            }
                            const col = statusColor(q.status, q.date);
                            return (
                              <div key={m.key} style={{ padding: "8px 6px", display: "flex", justifyContent: "center" }}>
                                <button onClick={() => setEditQbr(Object.assign({}, q))}
                                  title={q.client_name + " · " + q.quarter + " · " + q.status}
                                  style={{ padding: "3px 7px",
                                    background: "color-mix(in oklch, " + col + " 14%, transparent)",
                                    border: "1px solid color-mix(in oklch, " + col + " 40%, transparent)",
                                    borderRadius: 4, cursor: "pointer", fontSize: 10.5,
                                    fontFamily: "var(--f-mono)", color: col, whiteSpace: "nowrap" }}>
                                  {q.date ? q.date.slice(5) : "·"}
                                </button>
                              </div>
                            );
                          })}
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          </Card>
        )}
      </div>

      {/* Edit modal */}
      {editQbr && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.6)", display: "flex",
          alignItems: "center", justifyContent: "center", zIndex: 1000 }}
          onClick={(e) => { if (e.target === e.currentTarget) setEditQbr(null); }}>
          <div style={{ background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 8,
            padding: 24, width: 360, display: "flex", flexDirection: "column", gap: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontSize: 15, fontWeight: 500, color: "var(--ink-9)" }}>
                QBR · {editQbr.client_name}
              </div>
              <button onClick={() => setEditQbr(null)}
                style={{ background: "transparent", border: 0, cursor: "pointer", color: "var(--ink-6)", fontSize: 18 }}>✕</button>
            </div>

            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Квартал</span>
              <input value={editQbr.quarter || ""} readOnly
                style={{ padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4,
                  color: "var(--ink-7)", fontSize: 12, fontFamily: "var(--f-mono)", outline: "none" }}/>
            </label>

            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Дата проведения</span>
              <input type="date" value={editQbr.date || ""}
                onChange={(e) => setEditQbr(Object.assign({}, editQbr, { date: e.target.value }))}
                style={{ padding: "8px 10px", background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 4,
                  color: "var(--ink-8)", fontSize: 12, fontFamily: "var(--f-mono)", outline: "none" }}/>
            </label>

            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="mono" style={{ fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Статус</span>
              <select value={editQbr.status || "scheduled"}
                onChange={(e) => setEditQbr(Object.assign({}, editQbr, { status: e.target.value }))}
                style={{ padding: "8px 10px", background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 4,
                  color: "var(--ink-8)", fontSize: 12, fontFamily: "var(--f-mono)", outline: "none" }}>
                <option value="scheduled">scheduled</option>
                <option value="completed">completed</option>
                <option value="cancelled">cancelled</option>
                <option value="draft">draft</option>
              </select>
            </label>

            <div style={{ display: "flex", gap: 10, marginTop: 4 }}>
              <Btn kind="primary" size="m" disabled={saving} onClick={saveQbr}>
                {saving ? "Сохраняю…" : "Сохранить"}
              </Btn>
              <Btn kind="ghost" size="m" onClick={() => setEditQbr(null)}>Отмена</Btn>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { PageTop50, PageTasks, PageMeetings, PagePortfolio, PageAI, PageKanban, PageKPI, PageCabinet, PageTemplates, PageAuto, PageRoadmap, PageInternal, PageExtInstall, PageHelp, PageProfile, PageAssignments, PageQBR });


// ── PageIntegrations — единая страница всех интеграций ─────────────────────

function PageIntegrations() {
  const [status, setStatus] = React.useState(null);
  const [syncStatus, setSyncStatus] = React.useState(null);
  const [tab, setTab] = React.useState("overview");
  // Extension tab — tokens state
  const [extTokens, setExtTokens] = React.useState([]);
  const [extTokensLoading, setExtTokensLoading] = React.useState(false);
  const [extNewName, setExtNewName] = React.useState("");
  const [extNewToken, setExtNewToken] = React.useState(null);
  const HUB_URL = (typeof window !== "undefined" && window.__HUB_URL) || window.location.origin;

  const reloadExtTokens = React.useCallback(async () => {
    setExtTokensLoading(true);
    try {
      const r = await fetch("/api/me/api-tokens", { credentials: "include" });
      const d = r.ok ? await r.json() : { tokens: [] };
      setExtTokens(d.tokens || []);
    } catch (_) { setExtTokens([]); }
    finally { setExtTokensLoading(false); }
  }, []);

  const createExtToken = async () => {
    const name = (extNewName || "").trim();
    if (!name) return;
    const r = await fetch("/api/me/api-tokens", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (r.ok) {
      const d = await r.json();
      setExtNewToken(d.token);
      setExtNewName("");
      reloadExtTokens();
    } else { if (typeof appToast === "function") appToast("Ошибка: " + r.status, "error"); }
  };
  const revokeExtToken = async (id) => {
    if (!await appConfirm("Отозвать токен?")) return;
    const r = await fetch(`/api/me/api-tokens/${id}`, { method: "DELETE", credentials: "include" });
    if (r.ok) reloadExtTokens();
  };
  const copyToClip = (text, label) => {
    try { navigator.clipboard.writeText(text); if (typeof appToast === "function") appToast(`✓ ${label} скопирован`); }
    catch (_) { if (typeof appToast === "function") appToast("Копирование не поддерживается", "error"); }
  };

  // Lazy-load токенов при первом открытии вкладки extension
  React.useEffect(() => {
    if (tab === "extension" && extTokens.length === 0 && !extTokensLoading) {
      reloadExtTokens();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/me/integrations", { credentials: "include" });
        if (!r.ok) { if (!cancelled) setStatus({}); return; }
        const d = await r.json();
        if (!cancelled) setStatus(d || {});
      } catch (_) { if (!cancelled) setStatus({}); }
      try {
        const r2 = await fetch("/api/sync/status", { credentials: "include" });
        if (r2.ok) {
          const d2 = await r2.json();
          if (!cancelled) setSyncStatus(d2 || {});
        }
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const isAdmin = (typeof window !== "undefined" && window.__CURRENT_USER && window.__CURRENT_USER.role === "admin");

  const TABS = [
    { k: "overview",   l: "Обзор" },
    { k: "merchrules", l: "Merchrules" },
    { k: "airtable",   l: "Airtable" },
    { k: "time",       l: "Tbank Time" },
    { k: "ktalk",      l: "Ktalk" },
    { k: "telegram",   l: "Telegram" },
    { k: "diginetica", l: "Diginetica" },
    { k: "extension",  l: "Расширение" },
    { k: "help",       l: "Помощь" },
    ...(isAdmin ? [{ k: "backups", l: "Бэкапы" }] : []),
  ];

  const dotSpan = (on) => React.createElement("span", {
    style: { width: 8, height: 8, borderRadius: 999,
              background: on ? "var(--ok)" : "var(--ink-4)",
              boxShadow: on ? "0 0 6px var(--ok)" : "none",
              display: "inline-block" },
  });

  function Row(props) {
    return React.createElement("div", {
      style: { display: "flex", alignItems: "center", gap: 12, padding: "12px 14px",
                background: "var(--ink-2)", border: "1px solid var(--line)",
                borderRadius: 6, marginBottom: 8 },
    },
      dotSpan(props.ok),
      React.createElement("div", { style: { flex: 1 } },
        React.createElement("div", { style: { fontSize: 13, fontWeight: 500, color: "var(--ink-9)" } }, props.label),
        React.createElement("div", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-6)", marginTop: 2 } }, props.ok ? "подключено" : "не настроено"),
      ),
      props.children,
    );
  }

  // Блок ошибок синков — показываем если хоть один sync упал с error.
  const renderSyncErrors = () => {
    if (!syncStatus) return null;
    const errors = Object.entries(syncStatus).filter(([k, v]) => v && v.status === "error");
    if (!errors.length) return null;
    return React.createElement("div", {
      style: {
        padding: 14, marginBottom: 12,
        background: "rgba(240,70,58,.08)", border: "1px solid var(--critical-dim)",
        borderLeft: "3px solid var(--critical)", borderRadius: 4,
      }
    },
      React.createElement("div", { style: { fontSize: 13, fontWeight: 600, color: "var(--critical)", marginBottom: 8 } },
        "⚠ Ошибки последних синхронизаций (" + errors.length + ")"),
      errors.map(([k, v]) =>
        React.createElement("div", { key: k, style: { fontSize: 12, color: "var(--ink-8)", marginBottom: 6, fontFamily: "var(--f-mono)" } },
          React.createElement("span", { style: { color: "var(--critical)", fontWeight: 600 } }, k + ":"),
          " ", (v.message || v.error || "—"),
          v.ago && React.createElement("span", { style: { color: "var(--ink-5)", marginLeft: 8 } }, "· " + v.ago),
        )
      )
    );
  };

  const renderOverview = () => React.createElement("div", null,
    renderSyncErrors(),
    React.createElement(Row, { label: "Merchrules", ok: status && status.merchrules },
      React.createElement(Btn, { kind: "ghost", size: "s", onClick: () => setTab("merchrules") }, "Настроить"),
    ),
    React.createElement(Row, { label: "Airtable", ok: status && status.airtable },
      React.createElement(Btn, { kind: "ghost", size: "s", onClick: () => setTab("airtable") }, "Настроить"),
    ),
    React.createElement(Row, { label: "Tbank Time (тикеты)", ok: status && status.tbank_time },
      React.createElement(Btn, { kind: "primary", size: "s", onClick: () => { window.location.href = "/auth/time/login"; } }, status && status.tbank_time ? "Переподключить" : "Войти"),
    ),
    React.createElement(Row, { label: "Ktalk", ok: status && status.ktalk },
      React.createElement(Btn, { kind: "primary", size: "s", onClick: () => { window.open("https://tbank.ktalk.ru/", "_blank"); } }, "Открыть Ktalk"),
    ),
    React.createElement(Row, { label: "Telegram", ok: status && status.telegram },
      React.createElement(Btn, { kind: "ghost", size: "s", onClick: () => { window.location.href = "/design/profile"; } }, "Привязать"),
    ),
    React.createElement(Row, { label: "Diginetica (чекапы поиска)", ok: false },
      React.createElement(Btn, { kind: "ghost", size: "s", onClick: () => setTab("diginetica") }, "Настроить"),
    ),
    React.createElement(Row, { label: "Chrome-расширение", ok: true },
      React.createElement(Btn, { kind: "ghost", size: "s", onClick: () => setTab("extension") }, "Скачать"),
    ),
  );

  const renderTime = () => React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 14 } },
    React.createElement("div", { style: { fontSize: 13, color: "var(--ink-8)" } },
      "Подключение к time.tbank.ru → any-team-support через OAuth 2.0. Токен хранится на сервере, авторефреш.",
    ),
    React.createElement(Row, { label: "Статус OAuth", ok: status && status.tbank_time },
      React.createElement(Btn, { kind: "primary", size: "m",
        onClick: () => { window.location.href = "/auth/time/login"; }},
        status && status.tbank_time ? "🔄 Переподключить" : "🔑 Войти через OAuth"),
    ),
    React.createElement(Btn, { kind: "ghost", size: "m", onClick: async () => {
      try {
        const r = await fetch("/api/tickets/sync", { method: "POST", credentials: "include" });
        const d = await r.json();
        appToast(d.ok ? `Тикетов: новых ${d.ingested || 0}, обновлено ${d.updated || 0}` : ("Ошибка: " + (d.error || "—")), d.ok ? "ok" : "error");
      } catch (e) { appToast("Ошибка: " + e.message, "error"); }
    } }, "▶ Синхронизировать тикеты"),
  );

  const renderMr = () => React.createElement("div", null,
    React.createElement("div", { style: { fontSize: 13, color: "var(--ink-8)", marginBottom: 10 } },
      "Креды Merchrules — в Настройках. Плановый синк раз в час + кнопка на Командном центре."),
    React.createElement(Btn, { kind: "primary", size: "m", onClick: async () => {
      try {
        const r = await fetch("/api/sync/merchrules", { method: "POST", credentials: "include", headers: {"Content-Type": "application/json"}, body: "{}" });
        const d = await r.json();
        appToast(d.error ? ("❌ " + d.error) : (`✅ Клиентов: ${d.clients_synced || 0}, задач: ${d.tasks_synced || 0}`), d.error ? "error" : "ok");
      } catch (e) { appToast("Ошибка: " + e.message, "error"); }
    } }, "▶ Синхронизировать сейчас"),
  );

  const renderAt = () => React.createElement("div", null,
    React.createElement("div", { style: { fontSize: 13, color: "var(--ink-8)", marginBottom: 10 } },
      "Airtable PAT — в Settings. База appEAS1rPKpevoIel."),
    React.createElement(Btn, { kind: "primary", size: "m", onClick: async () => {
      try {
        const r = await fetch("/api/sync/airtable", { method: "POST", credentials: "include", headers: {"Content-Type": "application/json"}, body: "{}" });
        const d = await r.json();
        appToast(d.error ? ("❌ " + d.error) : (`✅ Клиентов: ${d.synced || 0}, оплат: ${d.payment_updated || 0}`), d.error ? "error" : "ok");
      } catch (e) { appToast("Ошибка: " + e.message, "error"); }
    } }, "▶ Синхронизировать сейчас"),
  );

  const renderDig = () => React.createElement("div", null,
    React.createElement("div", { style: { fontSize: 13, color: "var(--ink-8)", marginBottom: 10 } },
      "Diginetica Search API — для чекапов качества поиска. apiKey задаётся на клиенте (Client.diginetica_api_key)."),
    React.createElement("div", { style: { fontSize: 12.5, color: "var(--ink-6)" } }, "Запуск — с вкладки «Чекапы» на странице клиента."),
  );

  const renderKt = () => React.createElement("div", null,
    React.createElement("div", { style: { fontSize: 13, color: "var(--ink-8)", marginBottom: 10, lineHeight: 1.5 } },
      "Контур.Толк (Ktalk) — встречи, транскрипции, слоты. ",
      React.createElement("br"),
      "Ktalk не выставляет публичный OIDC endpoint, поэтому авторизация идёт через расширение AM Hub: открой ktalk.ru в соседней вкладке, залогинься — расширение захватит access_token автоматически и положит его в настройки хаба."),
    React.createElement("div", { style: { display: "flex", gap: 8, flexWrap: "wrap" } },
      React.createElement(Btn, { kind: "primary", size: "m", onClick: () => { window.open("https://tbank.ktalk.ru/", "_blank"); } }, "Открыть Ktalk"),
      React.createElement(Btn, { kind: "ghost", size: "m", onClick: () => setTab("extension") }, "Скачать расширение"),
    ),
  );

  const renderTg = () => React.createElement("div", null,
    React.createElement("div", { style: { fontSize: 13, color: "var(--ink-8)", marginBottom: 10 } },
      "Telegram-бот для утренних планов и алертов."),
    React.createElement(Btn, { kind: "ghost", size: "m", onClick: () => { window.location.href = "/design/profile"; } }, "Привязать Telegram"),
  );

  const renderExt = () => {
    return React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 16 } },
      React.createElement("div", { style: { fontSize: 13, color: "var(--ink-8)" } },
        "Chrome-расширение AM Hub — синк Merchrules, чекап Diginetica. Скачай .zip, установи в chrome://extensions → Load unpacked, и вставь URL+токен ниже в popup расширения."),

      React.createElement(Btn, {
        kind: "primary", size: "m",
        onClick: () => { window.open("/static/amhub-ext.zip", "_blank"); },
      }, "⬇ Скачать .zip"),

      // Блок: URL хаба
      React.createElement("div", null,
        React.createElement("div", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 } },
          "AM Hub · URL"),
        React.createElement("div", { style: { display: "flex", gap: 6 } },
          React.createElement("input", {
            readOnly: true, value: HUB_URL,
            style: {
              flex: 1, padding: "8px 10px",
              background: "var(--ink-1)", border: "1px solid var(--line)",
              borderRadius: 4, color: "var(--ink-9)",
              fontFamily: "var(--f-mono)", fontSize: 12, outline: "none",
            },
          }),
          React.createElement(Btn, {
            size: "s", kind: "ghost",
            onClick: () => copyToClip(HUB_URL, "Hub URL"),
          }, "📋 Копия"),
        ),
      ),

      // Блок: токены
      React.createElement("div", null,
        React.createElement("div", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 } },
          "AM Hub · токен (постоянный, отзывный)"),

        // Список существующих
        React.createElement("div", { style: { marginBottom: 8 } },
          extTokensLoading && React.createElement("div", { style: { fontSize: 11, color: "var(--ink-5)", padding: "4px 0" } }, "Загрузка…"),
          !extTokensLoading && extTokens.length === 0 && React.createElement("div", { style: { fontSize: 11, color: "var(--ink-5)", padding: "4px 0" } },
            "Нет активных токенов — создай первый ↓"),
          extTokens.map(t => React.createElement("div", {
            key: t.id,
            style: {
              display: "flex", alignItems: "center", gap: 6,
              padding: "6px 8px", background: "var(--ink-1)",
              border: "1px solid var(--line)", borderRadius: 4, marginBottom: 4,
            },
          },
            React.createElement("div", { style: { flex: 1, minWidth: 0 } },
              React.createElement("div", { style: { fontSize: 12, fontWeight: 600, color: "var(--ink-9)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } }, t.name),
              React.createElement("div", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)" } },
                t.prefix + "… " + (t.last_used_at ? "· использовался " + new Date(t.last_used_at).toLocaleDateString("ru-RU") : "· не использовался")),
            ),
            React.createElement("button", {
              onClick: () => revokeExtToken(t.id), title: "Отозвать",
              style: { background: "transparent", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-6)", cursor: "pointer", padding: "3px 8px", fontSize: 11 },
            }, "✕"),
          )),
        ),

        // Форма создания
        React.createElement("div", { style: { display: "flex", gap: 6 } },
          React.createElement("input", {
            value: extNewName, onChange: e => setExtNewName(e.target.value),
            placeholder: "Название (например: Chrome на ноуте)",
            onKeyDown: e => { if (e.key === "Enter") createExtToken(); },
            style: { flex: 1, padding: "7px 10px", background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-9)", fontSize: 12 },
          }),
          React.createElement(Btn, { size: "s", kind: "primary", onClick: createExtToken, disabled: !extNewName.trim() }, "+ Создать"),
        ),

        // Показ свежесозданного
        extNewToken && React.createElement("div", {
          style: {
            marginTop: 10, padding: 10,
            background: "rgba(163,230,53,.10)", border: "1px solid rgba(163,230,53,.35)",
            borderRadius: 4,
          },
        },
          React.createElement("div", { style: { fontSize: 11, fontWeight: 700, color: "#a3e635", marginBottom: 6 } },
            "⚠️ Сохрани токен — больше не покажем"),
          React.createElement("div", { style: { display: "flex", gap: 6, alignItems: "center" } },
            React.createElement("code", {
              className: "mono",
              style: { flex: 1, fontSize: 11, color: "var(--ink-9)", wordBreak: "break-all" },
            }, extNewToken),
            React.createElement(Btn, {
              size: "s", kind: "ghost",
              onClick: () => copyToClip(extNewToken, "Токен"),
            }, "📋"),
            React.createElement("button", {
              onClick: () => setExtNewToken(null),
              style: { background: "transparent", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-6)", cursor: "pointer", padding: "3px 8px", fontSize: 11 },
            }, "✕"),
          ),
        ),
      ),

      React.createElement("div", { style: { fontSize: 11.5, color: "var(--ink-6)", lineHeight: 1.5 } },
        "Подсказка: в popup расширения есть поля «AM Hub · URL» и «AM Hub · Token». Скопируй URL и токен отсюда и вставь туда."),
    );
  };

  const renderHelp = () => React.createElement("div", { style: { fontSize: 13, lineHeight: 1.6, color: "var(--ink-7)" } },
    React.createElement("h3", { style: { fontSize: 15, color: "var(--ink-9)", marginBottom: 8 } }, "FAQ"),
    React.createElement("p", null, "• Time-тикеты не тянутся — проверь OAuth (Войти)."),
    React.createElement("p", null, "• Клиенты Airtable не обновляются — проверь AIRTABLE_TOKEN в Railway."),
    React.createElement("p", null, "• Merchrules 401 — обнови логин/пароль в Settings."),
  );

  const BackupsTab = () => {
    const [items, setItems] = React.useState(null);
    const [busy, setBusy] = React.useState(false);
    const reload = React.useCallback(async () => {
      try {
        const r = await fetch("/api/admin/backups/list", { credentials: "include" });
        if (!r.ok) { setItems([]); return; }
        const d = await r.json();
        setItems(Array.isArray(d.items) ? d.items : []);
      } catch (_) { setItems([]); }
    }, []);
    React.useEffect(() => { reload(); }, [reload]);
    const runAll = async () => {
      setBusy(true);
      try {
        const r = await fetch("/api/admin/backups/run", { method: "POST", credentials: "include" });
        const d = await r.json().catch(() => ({}));
        if (typeof appToast === "function") {
          appToast(r.ok ? ("✅ Готово: " + (d.count != null ? d.count : (d.files || []).length) + " файлов") : ("Ошибка: " + (d.detail || r.status)), r.ok ? "ok" : "error");
        }
        await reload();
      } catch (e) {
        if (typeof appToast === "function") appToast("Ошибка: " + e.message, "error");
      } finally { setBusy(false); }
    };
    const del = async (filename) => {
      if (!confirm("Удалить " + filename + "?")) return;
      try {
        const r = await fetch("/api/admin/backups/" + encodeURIComponent(filename), { method: "DELETE", credentials: "include" });
        if (typeof appToast === "function") appToast(r.ok ? "Удалено" : "Ошибка удаления", r.ok ? "ok" : "error");
        await reload();
      } catch (e) {
        if (typeof appToast === "function") appToast("Ошибка: " + e.message, "error");
      }
    };
    const fmtSize = (n) => {
      if (n == null) return "—";
      if (n < 1024) return n + " B";
      if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
      return (n / 1024 / 1024).toFixed(2) + " MB";
    };
    return React.createElement("div", null,
      React.createElement("div", { style: { fontSize: 13, color: "var(--ink-8)", marginBottom: 10, lineHeight: 1.5 } },
        "Ежедневно в 03:00 MSK. Хранение 30 дней. Каждый файл — gzip-JSON снимок данных одного менеджера (клиенты, задачи, встречи, чекапы, заметки, QBR, тикеты и др.)."),
      React.createElement("div", { style: { marginBottom: 14 } },
        React.createElement(Btn, { kind: "primary", size: "m", disabled: busy, onClick: runAll },
          busy ? "Бэкап…" : "▶ Запустить для всех"),
      ),
      items == null
        ? React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12 } }, "Загрузка…")
        : items.length === 0
          ? React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12 } }, "Файлов пока нет.")
          : React.createElement("div", null,
              items.map((it) =>
                React.createElement("div", {
                  key: it.filename,
                  style: {
                    display: "flex", alignItems: "center", gap: 10,
                    padding: "10px 12px", marginBottom: 6,
                    background: "var(--ink-2)", border: "1px solid var(--line)",
                    borderRadius: 6,
                  },
                },
                  React.createElement("div", { style: { flex: 1, minWidth: 0 } },
                    React.createElement("div", { className: "mono", style: { fontSize: 12, color: "var(--ink-9)", wordBreak: "break-all" } }, it.filename),
                    React.createElement("div", { style: { fontSize: 11, color: "var(--ink-6)", marginTop: 2 } },
                      (it.mtime || "").replace("T", " ").slice(0, 16), " · ", fmtSize(it.size)),
                  ),
                  React.createElement("a", {
                    href: "/api/admin/backups/download/" + encodeURIComponent(it.filename),
                    style: {
                      fontSize: 12, padding: "5px 10px", borderRadius: 4,
                      border: "1px solid var(--line)", color: "var(--ink-8)",
                      textDecoration: "none",
                    },
                  }, "Скачать"),
                  React.createElement("button", {
                    onClick: () => del(it.filename),
                    style: {
                      fontSize: 12, padding: "5px 10px", borderRadius: 4,
                      border: "1px solid var(--line)", background: "transparent",
                      color: "var(--critical)", cursor: "pointer",
                    },
                  }, "Удалить"),
                )
              )
            ),
    );
  };
  const renderBackups = () => React.createElement(BackupsTab, null);

  const content = ({
    overview: renderOverview,
    merchrules: renderMr,
    airtable: renderAt,
    time: renderTime,
    ktalk: renderKt,
    telegram: renderTg,
    diginetica: renderDig,
    extension: renderExt,
    help: renderHelp,
    backups: renderBackups,
  })[tab]();

  return React.createElement("div", null,
    React.createElement(TopBar, {
      breadcrumbs: ["am hub", "интеграции"],
      title: "Интеграции",
      subtitle: "Все внешние сервисы в одном месте",
    }),
    React.createElement("div", { style: { padding: "22px 28px 40px", display: "grid", gridTemplateColumns: "200px 1fr", gap: 22 } },
      React.createElement("div", null,
        TABS.map(t =>
          React.createElement("button", {
            key: t.k,
            onClick: () => setTab(t.k),
            style: {
              display: "block", width: "100%", textAlign: "left",
              padding: "9px 12px", marginBottom: 2,
              background: tab === t.k ? "var(--signal)" : "transparent",
              color: tab === t.k ? "var(--ink-0)" : "var(--ink-7)",
              border: 0, borderRadius: 4, cursor: "pointer",
              fontFamily: "var(--f-mono)", fontSize: 11,
              textTransform: "uppercase", letterSpacing: "0.08em",
            },
          }, t.l)
        ),
      ),
      React.createElement(Card, { title: (TABS.find(x => x.k === tab) || {}).l || "Интеграции" }, content),
    ),
  );
}
window.PageIntegrations = PageIntegrations;
