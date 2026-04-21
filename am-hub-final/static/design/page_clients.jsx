// page_clients.jsx — Clients list & Client detail

function PageClients() {
  const P = (typeof window !== "undefined" && window.__PAGINATION) || { page: 1, total: 0, total_pages: 1, has_prev: false, has_next: false };
  const CL = (typeof window !== "undefined" && window.CLIENTS) || [];
  const [segFilter, setSegFilter] = React.useState("all");
  const [selectedIds, setSelectedIds] = React.useState(() => new Set());
  const [bulkBusy, setBulkBusy] = React.useState(false);
  const toggleSel = (id) => setSelectedIds(prev => {
    const s = new Set(prev);
    if (s.has(id)) s.delete(id); else s.add(id);
    return s;
  });
  const clearSel = () => setSelectedIds(new Set());
  // Tab switcher: список клиентов vs структура портфеля (бывшая страница /portfolio).
  const [view, setView] = React.useState(() => {
    try { return sessionStorage.getItem("amhub_portfolio_view") || "list"; } catch (_) { return "list"; }
  });
  const switchView = (v) => {
    setView(v);
    try { sessionStorage.setItem("amhub_portfolio_view", v); } catch (_) {}
  };

  // Если выбран режим «структура», рендерим PagePortfolio (глобальный компонент).
  if (view === "structure" && typeof window.PagePortfolio === "function") {
    return React.createElement("div", null,
      React.createElement("div", {
        style: { padding: "16px 28px 0 28px", display: "flex", gap: 8 },
      },
        React.createElement(Btn, { kind: "dim",     size: "s", onClick: () => switchView("list") },      "Список"),
        React.createElement(Btn, { kind: "primary", size: "s", onClick: () => switchView("structure") }, "Структура"),
      ),
      React.createElement(window.PagePortfolio),
    );
  }

  // Сегменты — реальные из models.py (ENT, SME+, SME, SME-, SMB, SS).
  // Плюс виртуальные: NEW (недавно добавлен) и RISK (churn).
  const _norm = (s) => (s || "").toUpperCase().replace(/\s+/g, "");
  const segments = [
    { key: "all",    label: "все",         match: () => true },
    { key: "ENT",    label: "ENT",         match: (c) => _norm(c.segment) === "ENT" },
    { key: "SME+",   label: "SME+",        match: (c) => _norm(c.segment) === "SME+" },
    { key: "SME",    label: "SME",         match: (c) => _norm(c.segment) === "SME" },
    { key: "SME-",   label: "SME-",        match: (c) => _norm(c.segment) === "SME-" },
    { key: "SMB",    label: "SMB",         match: (c) => _norm(c.segment) === "SMB" },
    { key: "SS",     label: "SS",          match: (c) => _norm(c.segment) === "SS" },
    { key: "NEW",    label: "NEW",         match: (c) => c.is_new || (c.days_since_added != null && c.days_since_added <= 14) },
    { key: "RISK",   label: "churn-риск",  match: (c) => c.status === "risk" || (c.health_score != null && c.health_score < 0.4) },
  ];
  const counted = segments.map(s => ({ ...s, n: CL.filter(s.match).length }));
  const activeSeg = counted.find(s => s.key === segFilter) || counted[0];
  const visibleClients = CL.filter(activeSeg.match);

  // Вытянуть клиентов из Airtable прямо здесь — одна кнопка.
  // reset:true очищает manager_email у всех клиентов текущего юзера,
  // потом sync переприсваивает по CSM из Airtable. Убирает «фантомных».
  const [syncBusy, setSyncBusy] = React.useState(false);
  async function pullFromAirtable() {
    setSyncBusy(true);
    try {
      const r = await fetch("/api/sync/airtable", {
        method: "POST", headers: {"Content-Type":"application/json"},
        credentials: "include", body: JSON.stringify({reset: true})
      });
      const d = await r.json().catch(() => ({}));
      if (d.error) {
        appToast("Airtable: " + d.error);
      } else {
        appToast(`Готово. ${d.message || JSON.stringify(d).slice(0, 200)}`);
        location.reload();
      }
    } catch (e) {
      appToast("Ошибка: " + e.message);
    } finally {
      setSyncBusy(false);
    }
  }

  return (
    <div>
      <TopBar
        breadcrumbs={["am hub", "мой портфель"]}
        title="Мой портфель"
        subtitle={`${P.total} клиентов · стр. ${P.page} из ${P.total_pages}`}
        actions={
          <>
            <Btn kind="primary" size="m" onClick={() => switchView("list")}>Список</Btn>
            <Btn kind="ghost"   size="m" onClick={() => switchView("structure")}>Структура</Btn>
            <Btn kind="ghost" size="m" onClick={pullFromAirtable} disabled={syncBusy}>
              {syncBusy ? "Тянем..." : "⟲ Из Airtable"}
            </Btn>
            <Btn kind="ghost" size="m" onClick={async () => {
              if (!await appConfirm("Объединить дубли клиентов по нормализованному имени? 'Yves Rocher' и 'yves-rocher' станут одним.")) return;
              const r = await fetch("/api/clients/auto-dedupe", {method:"POST", credentials:"include"});
              const d = await r.json().catch(()=>({}));
              appToast(d.ok ? `Объединено: ${d.merged}` : (d.error || "Ошибка"));
              if (d.ok) location.reload();
            }}>⎘ Дубли</Btn>
            <Btn kind="ghost" size="m" onClick={async () => {
              const pv = await fetch("/api/clients/garbage", {credentials:"include"}).then(r=>r.json()).catch(()=>({garbage:[]}));
              const list = (pv.garbage || []);
              if (!list.length) { appToast("Мусорных записей не найдено."); return; }
              const names = list.map(x => `#${x.id} ${x.name}`).join("\n");
              if (!await appConfirm(`Найдено ${list.length} мусорных записей. Удалить?\n\n${names}`)) return;
              const r = await fetch("/api/clients/garbage/cleanup", {
                method:"POST", credentials:"include",
                headers:{"Content-Type":"application/json"},
                body: JSON.stringify({ids: list.map(x => x.id)}),
              });
              const d = await r.json().catch(()=>({}));
              appToast(d.ok ? `Удалено: ${d.count}` : (d.error || "Ошибка"));
              if (d.ok) location.reload();
            }}>🗑 Чистка</Btn>
            <Btn kind="ghost" size="m" onClick={async () => {
              if (!await appConfirm("Полная чистка: удалить мусор + объединить дубли?")) return;
              const pv = await fetch("/api/clients/garbage", {credentials:"include"}).then(r=>r.json()).catch(()=>({garbage:[]}));
              const ids = (pv.garbage || []).map(x => x.id);
              let deleted = 0;
              if (ids.length) {
                const r1 = await fetch("/api/clients/garbage/cleanup", {
                  method:"POST", credentials:"include",
                  headers:{"Content-Type":"application/json"},
                  body: JSON.stringify({ids}),
                });
                const d1 = await r1.json().catch(()=>({}));
                deleted = d1.count || 0;
              }
              const r2 = await fetch("/api/clients/auto-dedupe", {method:"POST", credentials:"include"});
              const d2 = await r2.json().catch(()=>({}));
              appToast(`Готово:\n  удалено мусора: ${deleted}\n  объединено дублей: ${d2.merged || 0}`);
              location.reload();
            }}>✨ Всё сразу</Btn>
            <Btn kind="ghost" size="m" icon={<I.download size={14}/>}
              onClick={() => window.open("/api/clients/export?format=csv", "_blank")}>Экспорт</Btn>
          </>
        }
      />
      <div style={{ padding: "22px 28px 40px" }}>
        {selectedIds.size > 0 && (
          <BulkToolbar selectedIds={selectedIds} onClear={clearSel} busy={bulkBusy} setBusy={setBulkBusy}/>
        )}
        {/* filter chips */}
        <div style={{ display: "flex", gap: 6, marginBottom: 16, flexWrap: "wrap" }}>
          {counted.map((s) => {
            const active = s.key === segFilter;
            return (
              <button key={s.key} onClick={() => setSegFilter(s.key)} style={{
                padding: "6px 11px",
                background: active ? "var(--signal)" : "var(--ink-2)",
                color: active ? "var(--ink-0)" : "var(--ink-7)",
                border: `1px solid ${active ? "var(--signal)" : "var(--line)"}`,
                borderRadius: 4, fontFamily: "var(--f-mono)", fontSize: 11,
                textTransform: "uppercase", letterSpacing: "0.06em",
                cursor: "pointer",
              }}>{s.label} · {s.n}</button>
            );
          })}
        </div>

        {/* table */}
        <div style={{ background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
          <div style={{
            display: "grid",
            gridTemplateColumns: "26px 6px 2.2fr 1fr 1.4fr 1.2fr 30px",
            gap: 16,
            padding: "10px 18px",
            background: "var(--ink-1)",
            borderBottom: "1px solid var(--line)",
            fontFamily: "var(--f-mono)", fontSize: 10,
            textTransform: "uppercase", letterSpacing: "0.08em",
            color: "var(--ink-5)", alignItems: "center",
          }}>
            <span>
              <input type="checkbox"
                checked={visibleClients.length > 0 && visibleClients.every(c => selectedIds.has(c.id))}
                onChange={(e) => {
                  if (e.target.checked) {
                    const s = new Set(selectedIds);
                    visibleClients.forEach(c => s.add(c.id));
                    setSelectedIds(s);
                  } else {
                    const s = new Set(selectedIds);
                    visibleClients.forEach(c => s.delete(c.id));
                    setSelectedIds(s);
                  }
                }}
                style={{ margin: 0, cursor: "pointer" }}
                title="Выбрать всех на странице"/>
            </span>
            <span></span>
            <span>клиент</span>
            <span>gmv 30д</span>
            <span>динамика</span>
            <span>след. контакт</span>
            <span></span>
          </div>
          {visibleClients.length === 0 && (
            <div style={{ padding: "40px 20px", color: "var(--ink-6)", textAlign: "center", fontSize: 13 }}>
              В сегменте «{activeSeg.label}» клиентов нет.
            </div>
          )}
          {visibleClients.map((c, i) => {
            const statusTone = c.status === "risk" ? "critical" : c.status === "warn" ? "warn" : "ok";
            const isDown = (c.delta || "").startsWith("−");
            return (
              <div key={c.id}
                onClick={() => { window.location.href = "/design/client/" + c.id; }}
                style={{
                display: "grid",
                gridTemplateColumns: "26px 6px 2.2fr 1fr 1.4fr 1.2fr 30px",
                gap: 16,
                padding: "14px 18px",
                borderBottom: i === visibleClients.length - 1 ? "none" : "1px solid var(--line-soft)",
                alignItems: "center",
                cursor: "pointer",
                background: selectedIds.has(c.id) ? "color-mix(in oklch, var(--signal) 8%, transparent)" : undefined,
              }}>
                <span onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox"
                    checked={selectedIds.has(c.id)}
                    onChange={() => toggleSel(c.id)}
                    style={{ margin: 0, cursor: "pointer" }}/>
                </span>
                <span style={{
                  width: 6, height: 36, borderRadius: 2,
                  background: `var(--${statusTone})`,
                }}/>
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
                    <span style={{ fontSize: 13.5, fontWeight: 500, color: "var(--ink-9)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</span>
                    <Seg value={c.seg}/>
                    <StatDot tone={statusTone}>{c.status}</StatDot>
                  </div>
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>#{String(1000+c.id)} · {c.stage}</div>
                </div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 500, color: "var(--ink-9)", fontFamily: "var(--f-mono)", letterSpacing: "-0.01em" }}>{c.gmv}</div>
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>за 30 дней</div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <TrendBars data={c.trend} down={isDown}/>
                  <div className="mono" style={{
                    fontSize: 13, fontWeight: 500,
                    color: isDown ? "var(--critical)" : "var(--ok)",
                    whiteSpace: "nowrap",
                  }}>{c.delta}</div>
                </div>
                <div>
                  <div style={{ fontSize: 12.5, color: "var(--ink-8)" }}>{c.next}</div>
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>запланировано</div>
                </div>
                <I.arrow_r size={14} stroke="var(--ink-5)"/>
              </div>
            );
          })}
        </div>

        {/* pagination controls */}
        {P.total_pages > 1 && (
          <div style={{
            display: "flex", alignItems: "center", gap: 12,
            padding: "18px 2px 4px",
          }}>
            <span className="mono" style={{ fontSize: 11, color: "var(--ink-5)" }}>
              {((P.page - 1) * P.per_page) + 1}–{Math.min(P.page * P.per_page, P.total)} из {P.total}
            </span>
            <div style={{ flex: 1 }}/>
            <Btn kind="ghost" size="s"
              onClick={() => { if (P.has_prev) window.location.href = "?page=" + (P.page - 1); }}
              icon={<I.arrow_l size={12}/>}
            >Назад</Btn>
            <span className="mono" style={{ fontSize: 12, color: "var(--ink-7)", padding: "0 6px" }}>
              {P.page} / {P.total_pages}
            </span>
            <Btn kind="ghost" size="s"
              onClick={() => { if (P.has_next) window.location.href = "?page=" + (P.page + 1); }}
              iconRight={<I.arrow_r size={12}/>}
            >Вперёд</Btn>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Client detail page ─────────────────────────────────────
// window.__CURRENT_CLIENT — конкретный клиент, переданный с сервера
// (роут /design/client/{id}). Фоллбек на первого из списка, если открыли
// страницу без контекста — для отладки дизайна.
function PageClient() {
  // Fallbacks: server → window.__CURRENT_CLIENT; если нет — берём первого
  // из списка window.CLIENTS; если и его нет — null (показываем заглушку).
  const _clients = (typeof window !== "undefined" && window.CLIENTS) || [];
  const c = (typeof window !== "undefined" && window.__CURRENT_CLIENT) || _clients[0] || null;
  if (!c) {
    return (
      <div style={{ padding: 40, color: "var(--ink-6)" }}>
        Клиент не найден или нет доступа.
      </div>
    );
  }

  // Хелперы для реальных данных из БД
  const segment = c.segment || "—";
  const domain = c.domain || "—";
  const managerEmail = c.manager_email || "—";
  const health = c.health_score != null ? Math.round(c.health_score * 100) : null;
  const lastContact = c.last_meeting_date || c.last_checkup;
  const lastContactStr = lastContact ? new Date(lastContact).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : "никогда";
  const gmv = c.gmv != null ? "₽ " + (c.gmv >= 1e6 ? (c.gmv/1e6).toFixed(1) + "м" : c.gmv >= 1e3 ? (c.gmv/1e3).toFixed(0) + "к" : c.gmv) : "—";
  const openTasks = (c.tasks_open != null) ? c.tasks_open : (c.open_tasks != null ? c.open_tasks : null);

  // payment_status: active | overdue | suspended | trial | unknown
  const payMap = { active: "✓ активна", overdue: "⚠ просрочка", suspended: "⛔ приост.", trial: "🆓 триал" };
  const payToneMap = { active: "ok", overdue: "warn", suspended: "critical", trial: "signal" };
  const payStr = payMap[c.payment_status] || "—";
  const payTone = payToneMap[c.payment_status] || "neutral";

  const [followupModal, setFollowupModal] = React.useState(false);

  return (
    <div>
      {followupModal && <FollowupModal client={c} onClose={() => setFollowupModal(false)}/>}
      <TopBar
        breadcrumbs={["am hub", "клиенты", c.name]}
        title={c.name}
        subtitle={[segment !== "—" && `Сегмент ${segment}`, domain !== "—" && domain, managerEmail !== "—" && "AM: " + managerEmail].filter(Boolean).join(" · ")}
        actions={
          <>
            <Btn kind="ghost" size="m" icon={<I.chat size={14}/>} onClick={async () => {
              const txt = await appPrompt("Текст заметки по клиенту", {
                title: "Новая заметка", placeholder: "О чём договорились / что заметил…",
                okLabel: "Сохранить",
              });
              if (!txt || !txt.trim()) return;
              const r = await fetch(`/api/clients/${c.id}/notes`, {
                method: "POST", credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ content: txt.trim() }),
              });
              if (r.ok) { appToast("Заметка сохранена", "ok"); window.location.reload(); }
              else appToast("Не удалось сохранить заметку", "error");
            }}>Заметка</Btn>
            <Btn kind="ghost" size="m" icon={<I.cal size={14}/>} onClick={() => {
              window.location.href = `/design/meetings?client_id=${c.id}`;
            }}>Запланировать</Btn>
            <Btn kind="primary" size="m" icon={<I.lightning size={14}/>} onClick={() => setFollowupModal(true)}>Follow-up</Btn>
          </>
        }
      />
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>

        {/* Баннер передачи клиента (если есть pending incoming или outgoing) */}
        <ClientTransferSection client={c} currentUser={(typeof window !== "undefined" && window.__CURRENT_USER) || {}}
          onRefresh={() => window.location.reload()}/>

        {/* top strip — реальные данные клиента */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 12 }}>
          <KPI label="Сегмент" value={segment} tone={segment !== "—" ? "signal" : "neutral"}/>
          <KPI label="GMV · 30д" value={gmv} sub={c.revenue_trend || undefined}/>
          <KPI label="Health score" value={health != null ? String(health) : "—"} tone={health == null ? "neutral" : health < 40 ? "critical" : health < 70 ? "warn" : "ok"} sub={health == null ? "данные не синкнуты" : undefined}/>
          <KPI label="Оплата" value={payStr} tone={payTone}/>
          <KPI label="Открытых задач" value={openTasks != null ? String(openTasks) : "—"} sub={openTasks == null ? "синк не делался" : undefined}/>
          <KPI label="Последний контакт" value={lastContactStr} sub={c.domain ? "домен: " + c.domain : undefined}/>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 18 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0 }}>

            {/* Онбординг — per-client 10-step flow */}
            <ClientOnboardingCard clientId={c.id}/>

            {/* AI summary — real /api/ai/generate-prep */}
            <ClientAIBrief clientId={c.id}/>

            {/* QBR prep — /api/clients/{id}/qbr-prep */}
            <ClientQBRPrep clientId={c.id}/>

            {/* activity timeline — real /api/clients/{id}/timeline */}
            <ClientTimeline clientId={c.id}/>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0 }}>
            {/* Контакты — real /api/clients/{id}/contacts */}
            <ClientContactsList clientId={c.id}/>

            {/* Продукты — real /api/clients/{id}/products */}
            <ClientProductsList clientId={c.id}/>

            {/* Фиды — real /api/clients/{id}/feeds */}
            <ClientFeedsList clientId={c.id}/>

            {/* Чекапы — v2 */}
            <ClientCheckupsList clientId={c.id}/>

            {/* Роадмап клиента — per-client (Q1/Q2/Q3/Q4 + Бэклог) */}
            <ClientRoadmap clientId={c.id}/>

            {/* Upsell — активные предложения расширения */}
            <ClientUpsellCard clientId={c.id}/>

            {/* Merchrules-дашборд — синонимы / whitelist / blacklist / merch-rules */}
            <ClientMerchrulesDashboard clientId={c.id}/>

            {/* Голосовые заметки — запись/плеер/транскрипция */}
            <ClientVoiceNotes clientId={c.id}/>

            {/* История партнёра — real /api/clients/{id}/logs */}
            <ClientLogsList clientId={c.id}/>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── ClientRoadmap — квартальный план развития клиента ──────────────────
// Использует Task + source="roadmap" + task_type=Q1..Q4|backlog.
// GET /api/tasks?client_id=X&source=roadmap → список, группируем по task_type.
// POST /api/tasks → создать новый элемент в колонке
function ClientRoadmap({ clientId }) {
  const [items, setItems] = React.useState(null);
  const [adding, setAdding] = React.useState(null); // Q1 / Q2 / ... / backlog

  const COLS = [
    { key: "Q1",      l: "Q1 · готово",   tone: "ok"      },
    { key: "Q2",      l: "Q2 · в работе", tone: "signal"  },
    { key: "Q3",      l: "Q3 · план",     tone: "info"    },
    { key: "Q4",      l: "Q4 · идеи",     tone: "warn"    },
    { key: "backlog", l: "бэклог",        tone: "neutral" },
  ];

  const reload = React.useCallback(async () => {
    try {
      const r = await fetch(`/api/tasks?client_id=${clientId}&source=roadmap`, { credentials: "include" });
      if (!r.ok) { setItems([]); return; }
      const d = await r.json();
      setItems(d.tasks || []);
    } catch (e) { setItems([]); }
  }, [clientId]);

  React.useEffect(() => { reload(); }, [reload]);

  async function addItem(colKey) {
    const title = (window.prompt(`Добавить в «${colKey}»:`) || "").trim();
    if (!title) return;
    try {
      const r = await fetch("/api/tasks", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title, client_id: clientId, source: "roadmap",
          task_type: colKey, priority: "med", status: "plan",
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      appToast("Добавлено в " + colKey, "ok");
      reload();
    } catch (e) { appToast("Ошибка: " + e.message, "error"); }
  }

  async function removeItem(id) {
    if (!await appConfirm("Удалить пункт?")) return;
    try {
      await fetch("/api/tasks/" + id, { method: "DELETE", credentials: "include" });
      reload();
    } catch (e) { appToast("Ошибка: " + e.message, "error"); }
  }

  const grouped = {};
  COLS.forEach(c => { grouped[c.key] = []; });
  (items || []).forEach(t => {
    const key = (t.task_type || "backlog").toUpperCase() === "BACKLOG" ? "backlog" : (t.task_type || "backlog");
    if (grouped[key]) grouped[key].push(t);
    else grouped["backlog"].push(t);
  });

  return React.createElement(Card, { title: "Роадмап клиента" },
    items === null
      ? React.createElement("div", { style: { fontSize: 12.5, color: "var(--ink-6)", padding: "10px 0" } }, "Загрузка…")
      : COLS.map((c, ci) =>
          React.createElement("div", {
            key: c.key,
            style: {
              padding: "10px 0",
              borderBottom: ci === COLS.length - 1 ? "none" : "1px solid var(--line-soft)",
            },
          },
            React.createElement("div", {
              style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 },
            },
              React.createElement("span", {
                className: "mono",
                style: { fontSize: 10.5, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.08em" },
              }, c.l),
              React.createElement("button", {
                onClick: () => addItem(c.key),
                style: { background: "none", border: 0, color: "var(--ink-5)", cursor: "pointer", fontSize: 13, padding: 0, lineHeight: 1 },
                title: "Добавить",
              }, "+"),
            ),
            grouped[c.key].length === 0
              ? React.createElement("div", { style: { fontSize: 12, color: "var(--ink-5)", padding: "2px 0 4px" } }, "пусто")
              : grouped[c.key].map(t =>
                  React.createElement("div", {
                    key: t.id,
                    style: {
                      display: "flex", justifyContent: "space-between", gap: 8,
                      padding: "4px 0", alignItems: "center",
                    },
                  },
                    React.createElement("span", {
                      style: { fontSize: 12.5, color: "var(--ink-8)", flex: 1 },
                    }, t.title),
                    React.createElement("button", {
                      onClick: () => removeItem(t.id),
                      style: { background: "none", border: 0, color: "var(--ink-5)", cursor: "pointer", fontSize: 12, padding: 0 },
                      title: "Удалить",
                    }, "×"),
                  )
                )
          )
        )
  );
}
window.ClientRoadmap = ClientRoadmap;

window.PageClients = PageClients;
window.PageClient = PageClient;

// ── FollowupModal — AI-генерация фолоуапа + редактирование + копировать + send ──
function FollowupModal({ client, onClose }) {
  const [text, setText] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [copied, setCopied] = React.useState(false);
  const [sending, setSending] = React.useState(false);

  async function markSent() {
    if (!text.trim() || sending) return;
    setSending(true);
    try {
      const r = await fetch(`/api/clients/${client.id}/followup/send`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      window.appToast && window.appToast("✓ Follow-up отмечен как отправленный");
      onClose();
    } catch (e) {
      window.appToast && window.appToast("Ошибка: " + e.message);
    } finally { setSending(false); }
  }

  const run = React.useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await fetch("/api/ai/generate-followup", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: Number(client.id) }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      setText(d.text || d.brief || "");
    } catch (e) { setErr(e.message || "не удалось получить"); }
    finally { setLoading(false); }
  }, [client.id]);

  React.useEffect(() => { run(); }, [run]);

  async function copyToClipboard() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch (_) { appToast("Копирование не поддерживается браузером", "error"); }
  }

  return React.createElement("div", {
    onClick: (e) => { if (e.target === e.currentTarget) onClose(); },
    style: {
      position: "fixed", inset: 0, zIndex: 9998,
      background: "rgba(0,0,0,.55)", backdropFilter: "blur(3px)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
    },
  },
    React.createElement("div", {
      style: {
        background: "var(--ink-1)", border: "1px solid var(--line)",
        borderRadius: 10, maxWidth: 720, width: "100%",
        maxHeight: "85vh", display: "flex", flexDirection: "column",
        padding: 20, boxShadow: "0 24px 64px rgba(0,0,0,.5)",
      },
    },
      React.createElement("div", {
        style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 },
      },
        React.createElement("div", null,
          React.createElement("div", { style: { fontSize: 15, fontWeight: 600, color: "var(--ink-9)" } }, "Follow-up · " + (client.name || "клиент")),
          React.createElement("div", { style: { fontSize: 11.5, color: "var(--ink-6)", marginTop: 2 } }, "AI-черновик после встречи — отредактируй и скопируй"),
        ),
        React.createElement("button", {
          onClick: onClose,
          style: { background: "none", border: 0, color: "var(--ink-5)", cursor: "pointer", fontSize: 18, padding: 0, lineHeight: 1 },
        }, "×"),
      ),
      err && React.createElement("div", { style: { fontSize: 12.5, color: "var(--critical)", padding: "10px 0" } }, "Ошибка: " + err),
      loading && React.createElement("div", { style: { fontSize: 12.5, color: "var(--ink-6)", padding: "16px 0" } }, "Генерация через AI…"),
      !loading && !err && React.createElement("textarea", {
        value: text, onChange: (e) => setText(e.target.value), rows: 16,
        style: {
          flex: 1, width: "100%", resize: "vertical", minHeight: 260,
          background: "var(--ink-2)", color: "var(--ink-8)",
          border: "1px solid var(--line)", borderRadius: 4,
          padding: "10px 12px", fontSize: 13, fontFamily: "inherit",
          whiteSpace: "pre-wrap", lineHeight: 1.5,
        },
      }),
      React.createElement("div", { style: { display: "flex", gap: 8, marginTop: 14, justifyContent: "flex-end", flexWrap: "wrap" } },
        React.createElement(Btn, { size: "m", kind: "ghost", onClick: run, disabled: loading },
          loading ? "…" : "🔄 Перегенерировать"),
        React.createElement(Btn, { size: "m", kind: "ghost", onClick: copyToClipboard, disabled: !text },
          copied ? "✓ Скопировано" : "📋 Копировать"),
        React.createElement(Btn, { size: "m", kind: "ghost", onClick: onClose }, "Закрыть"),
        React.createElement(Btn, { size: "m", kind: "primary", onClick: markSent, disabled: !text.trim() || sending },
          sending ? "…" : "📤 Отправлено в TG"),
      ),
    ),
  );
}
window.FollowupModal = FollowupModal;

// Mini bar-chart trend indicator — 14 bars, color by direction.
// Clearer than a line sparkline at this size; last bar is emphasized.
function TrendBars({ data = [], down = false, w = 96, h = 28 }) {
  const n = data.length || 14;
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const range = max - min || 1;
  const barW = (w - (n - 1) * 2) / n;
  const color = down ? "var(--critical)" : "var(--signal)";
  const dim   = down ? "var(--critical-dim)" : "var(--signal-dim)";
  return (
    <svg width={w} height={h} style={{ display: "block", flexShrink: 0 }}>
      {data.map((v, i) => {
        const bh = Math.max(2, ((v - min) / range) * (h - 4));
        const last = i === data.length - 1;
        return (
          <rect
            key={i}
            x={i * (barW + 2)}
            y={h - bh - 2}
            width={barW}
            height={bh}
            fill={last ? color : dim}
            rx={1}
          />
        );
      })}
    </svg>
  );
}
window.TrendBars = TrendBars;


// ── Client detail: real data components ───────────────────────────────

function ClientAIBrief({ clientId }) {
  const [text, setText] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState(null);

  const reload = React.useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await fetch("/api/ai/generate-prep", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: Number(clientId) }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      setText(d.brief || d.text || d.prep_text || "");
    } catch (e) { setErr(e.message || "Не удалось получить AI-бриф"); }
    finally { setLoading(false); }
  }, [clientId]);

  React.useEffect(() => { reload(); }, [reload]);

  return React.createElement(Card, {
    title: "AI-бриф перед встречей",
    action: React.createElement(Badge, { tone: "signal" }, loading ? "генерация…" : "auto"),
  },
    err && React.createElement("div", { style: { fontSize: 12.5, color: "var(--critical)", padding: "10px 0" } }, "Ошибка: " + err),
    !err && !loading && !text && React.createElement("div", { style: { fontSize: 12.5, color: "var(--ink-6)", padding: "10px 0" } }, "Недостаточно данных — добавьте встречи/задачи и повторите."),
    !err && text && React.createElement("div", { style: { fontSize: 13.5, color: "var(--ink-8)", lineHeight: 1.6, whiteSpace: "pre-wrap" } }, text),
    React.createElement("div", { style: { display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" } },
      React.createElement(Btn, { size: "s", kind: "ghost", onClick: reload }, "Обновить"),
      React.createElement(Btn, { size: "s", kind: "ghost", onClick: () => { window.location.href = "/design/client/" + clientId; } }, "Открыть полный prep"),
    )
  );
}
window.ClientAIBrief = ClientAIBrief;


// ── ClientQBRPrep — сводка для подготовки к QBR ────────────────────────
function ClientQBRPrep({ clientId }) {
  const [data, setData] = React.useState(null);
  const [err, setErr]   = React.useState(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`/api/clients/${clientId}/qbr-prep`, { credentials: "include" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const d = await r.json();
        if (!cancelled) setData(d);
      } catch (e) { if (!cancelled) setErr(e.message); }
    })();
    return () => { cancelled = true; };
  }, [clientId]);

  const fmtMoney = (v) => {
    if (v == null) return "—";
    const n = Number(v);
    if (!isFinite(n)) return "—";
    if (n >= 1_000_000) return `₽ ${(n/1_000_000).toFixed(1)}м`;
    if (n >= 1_000)     return `₽ ${Math.round(n/1_000)}к`;
    return `₽ ${Math.round(n)}`;
  };
  const fmtPct = (v) => v == null ? null : `${v > 0 ? "+" : ""}${v}%`;
  const fmtDate = (iso) => { try { return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }); } catch { return "—"; } };
  const deltaTone = (v) => v == null ? "neutral" : v > 0 ? "ok" : v < 0 ? "critical" : "neutral";

  const header = React.createElement(Badge, { tone: "signal" }, "срез по клиенту");

  if (err) return React.createElement(Card, { title: "Подготовка к QBR", action: header },
    React.createElement("div", { style: { color: "var(--critical)", fontSize: 12.5, padding: "10px 0" } }, "Ошибка: " + err));
  if (!data) return React.createElement(Card, { title: "Подготовка к QBR", action: header },
    React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "10px 0" } }, "Загрузка…"));

  const gmv = data.gmv || {};
  const health = data.health || {};
  const top50 = data.top50 || {};
  const meetings = data.meetings || [];

  const mini = (label, value, tone, hint) => React.createElement("div", {
    style: { padding: 12, background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 4, minWidth: 0 }
  },
    React.createElement("div", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" } }, label),
    React.createElement("div", { style: { fontSize: 20, fontWeight: 500, color: tone ? `var(--${tone})` : "var(--ink-9)", marginTop: 4, letterSpacing: "-0.02em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } }, value),
    hint != null && React.createElement("div", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-6)", marginTop: 2 } }, hint),
  );

  const healthVal = health.current != null
    ? (health.current <= 1 ? Math.round(health.current * 100) : Math.round(health.current))
    : null;
  const healthTrend = health.trend != null
    ? (Math.abs(health.trend) < 2 ? "≈" : (health.trend > 0 ? `+${health.trend > 1 ? Math.round(health.trend) : (health.trend * 100).toFixed(0)}` : `${health.trend > -1 ? (health.trend * 100).toFixed(0) : Math.round(health.trend)}`))
    : null;

  const fmtTop50 = (v) => v == null ? "—" : (typeof v === "number" ? (v <= 1 ? `${(v*100).toFixed(1)}%` : String(v)) : String(v));

  return React.createElement(Card, { title: "Подготовка к QBR", action: header },
    // Row 1: GMV + Health + Checkups + Open tasks
    React.createElement("div", { style: { display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, marginBottom: 12 } },
      mini("GMV / MRR", fmtMoney(gmv.value), null, gmv.delta_pct != null ? `${fmtPct(gmv.delta_pct)} vs прошлый мес.` : "нет пред. периода"),
      mini("Health score", healthVal != null ? String(healthVal) : "—", healthVal == null ? null : healthVal >= 70 ? "ok" : healthVal >= 40 ? "warn" : "critical", healthTrend != null ? `тренд ${healthTrend}` : "без истории"),
      mini("Чекапов · квартал", String(data.checkups_count || 0), (data.checkups_count || 0) > 0 ? "signal" : null, "за 90 дней"),
      mini("Открытых задач", String(data.open_tasks_count || 0), (data.open_tasks_count || 0) > 0 ? "warn" : "ok"),
    ),

    // Row 2: Top-50 metrics
    React.createElement("div", { style: { padding: "10px 0", borderTop: "1px solid var(--line-soft)", borderBottom: "1px solid var(--line-soft)", marginBottom: 12 } },
      React.createElement("div", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 } }, "Top-50 · последний срез"),
      React.createElement("div", { style: { display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 } },
        React.createElement("div", null,
          React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-6)" } }, "NDCG "),
          React.createElement("span", { style: { fontSize: 13, fontWeight: 500, color: "var(--ink-9)" } }, fmtTop50(top50.ndcg)),
        ),
        React.createElement("div", null,
          React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-6)" } }, "Precision "),
          React.createElement("span", { style: { fontSize: 13, fontWeight: 500, color: "var(--ink-9)" } }, fmtTop50(top50.precision)),
        ),
        React.createElement("div", null,
          React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-6)" } }, "Конверсия "),
          React.createElement("span", { style: { fontSize: 13, fontWeight: 500, color: "var(--ink-9)" } }, fmtTop50(top50.conversion)),
        ),
      ),
    ),

    // Row 3: Meetings
    React.createElement("div", null,
      React.createElement("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 } },
        React.createElement("span", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" } }, "Встречи за квартал"),
        React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-6)" } }, String(data.meetings_count || 0)),
      ),
      meetings.length === 0 && React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "8px 0" } }, "Встреч не было."),
      meetings.slice(0, 6).map((m, i) => React.createElement("div", {
        key: m.id,
        style: {
          display: "grid", gridTemplateColumns: "80px 1fr auto",
          gap: 10, alignItems: "center",
          padding: "8px 0",
          borderBottom: i === Math.min(meetings.length, 6) - 1 ? "none" : "1px solid var(--line-soft)",
        },
      },
        React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-5)" } }, fmtDate(m.date)),
        React.createElement("span", { style: { fontSize: 12.5, color: "var(--ink-8)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } }, m.title),
        m.is_qbr
          ? React.createElement(Badge, { tone: "signal" }, "QBR")
          : React.createElement("span", { className: "mono", style: { fontSize: 10, color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.06em" } }, m.type || "—"),
      )),
    ),
  );
}
window.ClientQBRPrep = ClientQBRPrep;


function ClientTimeline({ clientId }) {
  const [events, setEvents] = React.useState(null);
  const [err, setErr] = React.useState(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`/api/clients/${clientId}/timeline?limit=20`, { credentials: "include" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const d = await r.json();
        if (!cancelled) setEvents(d.events || []);
      } catch (e) { if (!cancelled) setErr(e.message); }
    })();
    return () => { cancelled = true; };
  }, [clientId]);

  const relDay = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    const diff = Math.round((Date.now() - d.getTime()) / 86400000);
    if (diff === 0) return "сегодня";
    if (diff === 1) return "вчера";
    if (diff < 0) return "через " + (-diff) + " дн";
    return diff + " дн назад";
  };
  const toneMap = { meeting: "signal", task_done: "ok", note: "neutral", checkup: "ok", qbr: "signal", history: "neutral" };

  if (err) return React.createElement(Card, { title: "Хронология" },
    React.createElement("div", { style: { color: "var(--critical)", fontSize: 12.5, padding: "10px 0" } }, "Ошибка: " + err));
  if (events === null) return React.createElement(Card, { title: "Хронология" },
    React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "10px 0" } }, "Загрузка..."));
  if (!events.length) return React.createElement(Card, { title: "Хронология" },
    React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "10px 0" } }, "Событий ещё нет."));

  return React.createElement(Card, { title: "Хронология" },
    React.createElement("div", { style: { position: "relative", paddingLeft: 20 } },
      React.createElement("div", { style: { position: "absolute", left: 5, top: 4, bottom: 4, width: 1, background: "var(--line)" } }),
      events.map((e, i) => {
        const tone = toneMap[e.type] || "neutral";
        const color = tone === "signal" ? "var(--signal)" : tone === "ok" ? "var(--ok)" : "var(--ink-4)";
        const title = e.title || e.content || e.field || e.type;
        return React.createElement("div", { key: i, style: { position: "relative", paddingBottom: 18 } },
          React.createElement("div", { style: {
            position: "absolute", left: -20, top: 4, width: 11, height: 11, borderRadius: 999,
            background: "var(--ink-1)", border: `2px solid ${color}`,
          } }),
          React.createElement("div", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" } }, relDay(e.date)),
          React.createElement("div", { style: { fontSize: 13.5, color: "var(--ink-9)", fontWeight: 500, marginTop: 2 } }, title),
          e.author && React.createElement("div", { style: { fontSize: 12, color: "var(--ink-6)", marginTop: 2 } }, e.author),
        );
      })
    )
  );
}
window.ClientTimeline = ClientTimeline;


function ClientContactsList({ clientId }) {
  const [list, setList] = React.useState(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`/api/clients/${clientId}/contacts`, { credentials: "include" });
        if (!r.ok) { if (!cancelled) setList([]); return; }
        const d = await r.json();
        const arr = d.contacts || d || [];
        if (!cancelled) setList(Array.isArray(arr) ? arr : []);
      } catch (e) { if (!cancelled) setList([]); }
    })();
    return () => { cancelled = true; };
  }, [clientId]);

  const roleMap = { decision_maker: "ЛПР", tech: "Технический", finance: "Финансы", other: "Другое" };

  if (list === null) return React.createElement(Card, { title: "Контакты" },
    React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "10px 0" } }, "Загрузка..."));
  if (!list.length) return React.createElement(Card, { title: "Контакты" },
    React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "10px 0" } },
      "Контактов нет. Добавятся при синке Airtable."));

  return React.createElement(Card, { title: "Контакты" },
    list.map((p, i) => React.createElement("div", {
      key: p.id || i,
      style: {
        display: "flex", alignItems: "center", gap: 10,
        padding: "10px 0",
        borderBottom: i === list.length - 1 ? "none" : "1px solid var(--line-soft)",
      }
    },
      React.createElement(Avatar, { name: p.name || "?", size: 32 }),
      React.createElement("div", { style: { flex: 1, minWidth: 0 } },
        React.createElement("div", { style: { fontSize: 12.5, fontWeight: 500, color: "var(--ink-8)" } }, p.name || "—"),
        React.createElement("div", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-5)" } },
          [roleMap[p.role] || p.role || "", p.position || ""].filter(Boolean).join(" · ") || "—"),
      ),
      React.createElement("span", { className: "mono", style: { fontSize: 10, color: "var(--ink-6)" } },
        p.email || p.phone || p.telegram || ""),
    ))
  );
}
window.ClientContactsList = ClientContactsList;


// ── Checkup v2 — список чекапов клиента + модалка создания/редактирования ──

function ClientCheckupsList({ clientId }) {
  const [list, setList] = React.useState(null);
  const [editing, setEditing] = React.useState(null); // null | {} | checkup obj

  const reload = React.useCallback(async () => {
    try {
      const r = await fetch(`/api/clients/${clientId}/checkups`, { credentials: "include" });
      if (!r.ok) { setList([]); return; }
      const d = await r.json();
      setList(d.checkups || []);
    } catch (e) { setList([]); }
  }, [clientId]);

  React.useEffect(() => { reload(); }, [reload]);

  const statusTone = { draft: "neutral", in_progress: "warn", done: "ok", overdue: "critical" };
  const statusLabel = { draft: "Черновик", in_progress: "В работе", done: "Завершён", overdue: "Просрочен" };

  function autoName() {
    const d = new Date();
    const months = ["январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"];
    return `Чек-ап качества поиска — ${months[d.getMonth()]} ${d.getFullYear()}`;
  }

  async function createOne() {
    const name = autoName();
    try {
      const r = await fetch(`/api/clients/${clientId}/checkups`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, frequency: "monthly" }),
      });
      const d = await r.json();
      if (d.id) {
        appToast("Чекап создан", "ok");
        setEditing(d);
        reload();
      } else {
        appToast("Ошибка: " + (d.error || "не удалось"), "error");
      }
    } catch (e) { appToast("Ошибка: " + e.message, "error"); }
  }

  async function removeOne(id) {
    if (!await appConfirm("Удалить чекап?")) return;
    await fetch(`/api/checkups/${id}`, { method: "DELETE", credentials: "include" });
    reload();
  }

  const action = React.createElement(Btn, {
    size: "s", kind: "primary", icon: React.createElement(I.plus, {size: 12}),
    onClick: createOne,
  }, "Новый");

  const body = (() => {
    if (list === null) return React.createElement("div", {style: {fontSize: 12.5, color: "var(--ink-6)", padding: "10px 0"}}, "Загрузка…");
    if (!list.length) return React.createElement("div", {style: {fontSize: 12.5, color: "var(--ink-6)", padding: "10px 0"}},
      "Чекапов пока нет. Нажмите «Новый» для создания.");
    return React.createElement("div", null, list.map((c, i) =>
      React.createElement("div", {
        key: c.id,
        style: {
          display: "grid", gridTemplateColumns: "1fr auto auto", gap: 8,
          padding: "10px 0",
          borderBottom: i === list.length - 1 ? "none" : "1px solid var(--line-soft)",
          alignItems: "center",
        }
      },
        React.createElement("div", {style: {minWidth: 0, cursor: "pointer"}, onClick: () => setEditing(c)},
          React.createElement("div", {style: {fontSize: 12.5, fontWeight: 500, color: "var(--ink-8)"}}, c.name),
          React.createElement("div", {className: "mono", style: {fontSize: 10.5, color: "var(--ink-5)"}},
            `${c.frequency || "monthly"} · ${c.score != null ? `${c.score}/${c.score_max}` : "не оценён"}`),
        ),
        React.createElement(Badge, {tone: statusTone[c.status] || "neutral", dot: true},
          statusLabel[c.status] || c.status),
        React.createElement("button", {
          onClick: () => removeOne(c.id),
          style: {background: "none", border: 0, color: "var(--ink-5)", cursor: "pointer", fontSize: 14},
          title: "Удалить",
        }, "✕"),
      )
    ));
  })();

  return React.createElement(React.Fragment, null,
    React.createElement(Card, {title: "Чекапы", action}, body),
    editing && React.createElement(CheckupWizard, {
      checkup: editing,
      clientId,
      onClose: () => { setEditing(null); reload(); },
    }),
  );
}
window.ClientCheckupsList = ClientCheckupsList;


// ── ClientLogsList — единая история партнёра (PartnerLog) ─────────────────
// Card «История партнёра»:
//  - форма сверху: event_type (select) + body (textarea) + кнопка «Добавить запись»
//  - лента записей: иконка типа, title/body (первые 200 символов), дата, автор
//  - кнопка «🗑» — удалить запись
function ClientLogsList({ clientId }) {
  const [list, setList] = React.useState(null);
  const [eventType, setEventType] = React.useState("note");
  const [bodyText, setBodyText] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  const EVENT_TYPES = [
    { k: "note",                 l: "Заметка",            icon: "📝" },
    { k: "communication",        l: "Коммуникация",       icon: "💬" },
    { k: "call",                 l: "Звонок",             icon: "📞" },
    { k: "email",                l: "Email",              icon: "✉️" },
    { k: "meeting_summary",      l: "Итоги встречи",      icon: "🤝" },
    { k: "merch_rule_created",   l: "Мерч-правило (new)", icon: "⚙️" },
    { k: "merch_rule_updated",   l: "Мерч-правило (upd)", icon: "⚙️" },
    { k: "synonym_added",        l: "Синоним добавлен",   icon: "🔤" },
    { k: "whitelist_added",      l: "Whitelist",          icon: "✅" },
    { k: "manual",               l: "Прочее",             icon: "•"  },
  ];
  const typeMeta = (t) => EVENT_TYPES.find(x => x.k === t) || { k: t, l: t, icon: "•" };

  const reload = React.useCallback(async () => {
    try {
      const r = await fetch(`/api/clients/${clientId}/logs?limit=50`, { credentials: "include" });
      if (!r.ok) { setList([]); return; }
      const d = await r.json();
      setList(d.logs || []);
    } catch (e) { setList([]); }
  }, [clientId]);

  React.useEffect(() => { reload(); }, [reload]);

  async function addOne() {
    const text = (bodyText || "").trim();
    if (!text) { appToast("Введите текст записи", "error"); return; }
    setSaving(true);
    try {
      const r = await fetch(`/api/clients/${clientId}/logs`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_type: eventType, body: text, source: "manual" }),
      });
      const d = await r.json().catch(() => ({}));
      if (d.ok) {
        setBodyText("");
        appToast("Запись добавлена", "ok");
        reload();
      } else {
        appToast("Ошибка: " + (d.error || d.detail || "не удалось"), "error");
      }
    } catch (e) { appToast("Ошибка: " + e.message, "error"); }
    finally { setSaving(false); }
  }

  async function removeOne(id) {
    if (!await appConfirm("Удалить запись из истории?")) return;
    try {
      await fetch(`/api/clients/${clientId}/logs/${id}`, {
        method: "DELETE", credentials: "include",
      });
      reload();
    } catch (e) { appToast("Ошибка: " + e.message, "error"); }
  }

  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  }

  // Форма добавления новой записи.
  const form = React.createElement("div", {
    style: {
      display: "grid", gridTemplateColumns: "1fr", gap: 8,
      padding: "10px 0 12px 0",
      borderBottom: "1px solid var(--line-soft)",
    },
  },
    React.createElement("div", { style: { display: "flex", gap: 8, alignItems: "center" } },
      React.createElement("select", {
        value: eventType,
        onChange: (e) => setEventType(e.target.value),
        style: {
          flex: "0 0 auto",
          background: "var(--ink-1)", color: "var(--ink-8)",
          border: "1px solid var(--line)", borderRadius: 4,
          fontSize: 12, padding: "6px 8px",
        },
      },
        EVENT_TYPES.map(t =>
          React.createElement("option", { key: t.k, value: t.k }, `${t.icon}  ${t.l}`)
        ),
      ),
    ),
    React.createElement("textarea", {
      value: bodyText,
      onChange: (e) => setBodyText(e.target.value),
      placeholder: "Текст записи (что произошло / о чём договорились / детали)…",
      rows: 3,
      style: {
        width: "100%", resize: "vertical",
        background: "var(--ink-1)", color: "var(--ink-8)",
        border: "1px solid var(--line)", borderRadius: 4,
        fontSize: 12.5, padding: "8px 10px",
        fontFamily: "inherit",
      },
    }),
    React.createElement("div", { style: { display: "flex", justifyContent: "flex-end" } },
      React.createElement(Btn, {
        size: "s", kind: "primary", disabled: saving,
        icon: React.createElement(I.plus, { size: 12 }),
        onClick: addOne,
      }, saving ? "Сохраняем…" : "Добавить запись"),
    ),
  );

  // Лента записей.
  const feed = (() => {
    if (list === null) return React.createElement("div",
      { style: { fontSize: 12.5, color: "var(--ink-6)", padding: "10px 0" } }, "Загрузка…");
    if (!list.length) return React.createElement("div",
      { style: { fontSize: 12.5, color: "var(--ink-6)", padding: "10px 0" } },
      "Записей ещё нет. Добавьте первую заметку.");
    return React.createElement("div", null,
      list.map((l, i) => {
        const tm = typeMeta(l.event_type);
        const raw = l.body || "";
        const short = raw.slice(0, 200) + (raw.length > 200 ? "…" : "");
        return React.createElement("div", {
          key: l.id,
          style: {
            display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 10,
            padding: "10px 0",
            borderBottom: i === list.length - 1 ? "none" : "1px solid var(--line-soft)",
            alignItems: "flex-start",
          },
        },
          React.createElement("div", {
            style: { fontSize: 16, lineHeight: "18px", width: 22, textAlign: "center", flexShrink: 0 },
            title: tm.l,
          }, tm.icon),
          React.createElement("div", { style: { minWidth: 0 } },
            l.title && React.createElement("div", {
              style: { fontSize: 12.5, fontWeight: 500, color: "var(--ink-8)", marginBottom: 2 },
            }, l.title),
            short && React.createElement("div", {
              style: { fontSize: 12, color: "var(--ink-7)", whiteSpace: "pre-wrap", wordBreak: "break-word" },
            }, short),
            React.createElement("div", {
              className: "mono",
              style: { fontSize: 10.5, color: "var(--ink-5)", marginTop: 4 },
            }, `${fmtDate(l.created_at)} · ${tm.l}${l.created_by ? " · " + l.created_by : ""}`),
          ),
          React.createElement("button", {
            onClick: () => removeOne(l.id),
            style: {
              background: "none", border: 0, color: "var(--ink-5)",
              cursor: "pointer", fontSize: 14, padding: 0, lineHeight: 1,
            },
            title: "Удалить запись",
          }, "🗑"),
        );
      })
    );
  })();

  return React.createElement(Card, { title: "История партнёра" },
    form,
    feed,
  );
}
window.ClientLogsList = ClientLogsList;


// ── CheckupWizard — фулскрин-модалка с табами ──────────────────────────────

function CheckupWizard({ checkup, clientId, onClose }) {
  const [tab, setTab] = React.useState(0);
  const [c, setC] = React.useState(checkup);
  const [queries, setQueries] = React.useState([]);
  const [subTab, setSubTab] = React.useState("top");
  const [saving, setSaving] = React.useState(false);
  const [running, setRunning] = React.useState(false);

  const TABS = [
    "Основные", "Поиск", "Трекинг", "UI/UX",
    "Рекомендации", "Отзывы", "Продукты", "Задолженность",
  ];
  const SUBTABS = [
    {k: "top", l: "Топ запросы"},
    {k: "random", l: "Случайные"},
    {k: "zero", l: "Нулевые"},
    {k: "zero_queries", l: "ZeroQueries"},
  ];

  React.useEffect(() => {
    (async () => {
      if (!c.id) return;
      try {
        const r = await fetch(`/api/checkups/${c.id}`, { credentials: "include" });
        if (r.ok) {
          const d = await r.json();
          setC(d.checkup);
          setQueries(d.queries || []);
        }
      } catch (e) {}
    })();
  }, [c.id]);

  async function save(patch) {
    setSaving(true);
    try {
      const r = await fetch(`/api/checkups/${c.id}`, {
        method: "PATCH", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (r.ok) { const d = await r.json(); setC({...c, ...d}); }
    } catch (e) { appToast("Ошибка сохранения: " + e.message, "error"); }
    setSaving(false);
  }

  async function addEmptyQuery() {
    const q = window.prompt("Поисковый запрос:");
    if (!q || !q.trim()) return;
    const r = await fetch(`/api/checkups/${c.id}/queries`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group: subTab, queries: [{ query: q.trim() }] }),
    });
    if (r.ok) {
      const full = await fetch(`/api/checkups/${c.id}`, { credentials: "include" }).then(x => x.json());
      setQueries(full.queries || []);
    }
  }

  async function loadFromAnalytics() {
    try {
      const r = await fetch(`/api/clients/${clientId}/analytics/queries?period_days=30&limit=30&q_type=${subTab}`,
        { credentials: "include" });
      const d = await r.json();
      const list = d.queries || [];
      if (!list.length) {
        appToast(d.message || "Аналитика пуста — источник не подключён", "warn", { duration: 8000 });
        return;
      }
      const add = await fetch(`/api/checkups/${c.id}/queries`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group: subTab, queries: list }),
      });
      const d2 = await add.json();
      appToast(`Добавлено ${d2.added || 0} запросов`, "ok");
      const full = await fetch(`/api/checkups/${c.id}`, { credentials: "include" }).then(x => x.json());
      setQueries(full.queries || []);
    } catch (e) { appToast("Ошибка: " + e.message, "error"); }
  }

  // Маппинг активного подтаба → kind для Merchrules analytics
  // top / random — как есть; zero (Нулевые) → null; zero_queries (ZeroQueries) → zero.
  const SUBTAB_TO_MR_KIND = { top: "top", random: "random", zero: "null", zero_queries: "zero" };

  async function loadFromMerchrules() {
    const kind = SUBTAB_TO_MR_KIND[subTab] || "top";
    try {
      const r = await fetch(`/api/checkup/${c.id}/load-queries-from-merchrules`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, limit: 30 }),
      });
      const d = await r.json();
      if (!r.ok || !d.ok) {
        appToast("Merchrules: " + (d.error || d.detail || ("HTTP " + r.status)), "error", { duration: 9000 });
        return;
      }
      appToast(`Merchrules: добавлено ${d.count || 0} запросов`, "ok");
      const full = await fetch(`/api/checkups/${c.id}`, { credentials: "include" }).then(x => x.json());
      setQueries(full.queries || []);
    } catch (e) { appToast("Ошибка: " + e.message, "error"); }
  }

  async function importCsv(evt) {
    const file = evt.target.files[0]; if (!file) return;
    const text = await file.text();
    const lines = text.split(/\r?\n/).filter(Boolean);
    const parsed = lines.map(line => {
      const [query, shows] = line.split(/[,;\t]/).map(s => s.trim().replace(/^"|"$/g, ""));
      return { query, shows_count: parseInt(shows || "0", 10) || 0 };
    }).filter(x => x.query && x.query.toLowerCase() !== "query" && x.query !== "запрос");
    if (!parsed.length) { appToast("CSV пустой", "warn"); return; }
    const r = await fetch(`/api/checkups/${c.id}/queries`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group: subTab, queries: parsed }),
    });
    const d = await r.json();
    appToast(`Импортировано: ${d.added}`, "ok");
    const full = await fetch(`/api/checkups/${c.id}`, { credentials: "include" }).then(x => x.json());
    setQueries(full.queries || []);
    evt.target.value = "";
  }

  async function updateQueryField(q, field, value) {
    await fetch(`/api/checkup-queries/${q.id}`, {
      method: "PATCH", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: value }),
    });
    setQueries(queries.map(x => x.id === q.id ? { ...x, [field]: value } : x));
  }

  async function deleteQuery(qid) {
    if (!await appConfirm("Удалить запрос?")) return;
    await fetch(`/api/checkup-queries/${qid}`, { method: "DELETE", credentials: "include" });
    setQueries(queries.filter(q => q.id !== qid));
  }

  async function runCheckup() {
    if (!await appConfirm("Запустить проверку через Diginetica?\nНужен apiKey Diginetica у клиента.")) return;
    setRunning(true);
    try {
      const r = await fetch(`/api/checkups/${c.id}/run`, { method: "POST", credentials: "include" });
      const d = await r.json();
      if (d.ok) {
        appToast(`Готово: оценено ${d.scored}, средний балл ${d.avg_score ?? "—"}`, "ok", { duration: 8000 });
        const full = await fetch(`/api/checkups/${c.id}`, { credentials: "include" }).then(x => x.json());
        setC(full.checkup); setQueries(full.queries || []);
      } else {
        appToast("Ошибка: " + (d.error || "не удалось"), "error", { duration: 8000 });
      }
    } catch (e) { appToast("Ошибка: " + e.message, "error"); }
    setRunning(false);
  }

  const filtered = queries.filter(q => q.group === subTab);

  const renderMain = () => React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 14 } },
    React.createElement("div", null,
      React.createElement("label", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" } }, "Название *"),
      React.createElement("input", {
        type: "text", value: c.name || "",
        onChange: e => setC({...c, name: e.target.value}),
        onBlur: () => save({ name: c.name }),
        style: { width: "100%", padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-8)", fontSize: 13, marginTop: 6 },
      }),
      React.createElement("button", {
        onClick: () => { const n = `Чек-ап качества поиска — ${["январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"][new Date().getMonth()]} ${new Date().getFullYear()}`; setC({...c, name: n}); save({name: n}); },
        style: { marginTop: 6, background: "transparent", border: "1px solid var(--line)", borderRadius: 4, padding: "6px 10px", color: "var(--ink-7)", fontSize: 11, cursor: "pointer" },
      }, "⚡ Автогенерация"),
    ),
    React.createElement("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 } },
      React.createElement("div", null,
        React.createElement("label", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase" } }, "Частота *"),
        React.createElement("select", {
          value: c.frequency || "monthly",
          onChange: e => { setC({...c, frequency: e.target.value}); save({ frequency: e.target.value }); },
          style: { width: "100%", padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-8)", marginTop: 6 },
        },
          React.createElement("option", {value: "monthly"}, "Ежемесячно"),
          React.createElement("option", {value: "quarterly"}, "Ежеквартально"),
          React.createElement("option", {value: "semiannual"}, "Раз в полгода"),
          React.createElement("option", {value: "yearly"}, "Ежегодно"),
          React.createElement("option", {value: "custom"}, "Произвольно"),
        ),
      ),
      React.createElement("div", null,
        React.createElement("label", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase" } }, "Срок выполнения"),
        React.createElement("input", {
          type: "date",
          value: c.due_date ? c.due_date.slice(0,10) : "",
          onChange: e => { const v = e.target.value ? e.target.value + "T12:00:00" : null; setC({...c, due_date: v}); save({ due_date: v }); },
          style: { width: "100%", padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-8)", marginTop: 6 },
        }),
      ),
    ),
    React.createElement("div", null,
      React.createElement("label", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase" } }, "Комментарий партнёра"),
      React.createElement("textarea", {
        value: c.partner_comment || "", rows: 4,
        onChange: e => setC({...c, partner_comment: e.target.value}),
        onBlur: () => save({ partner_comment: c.partner_comment }),
        style: { width: "100%", padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-8)", fontSize: 13, marginTop: 6, fontFamily: "inherit", resize: "vertical" },
      }),
    ),
    React.createElement("div", null,
      React.createElement("label", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase" } }, "Комментарий Any"),
      React.createElement("textarea", {
        value: c.any_comment || "", rows: 4,
        onChange: e => setC({...c, any_comment: e.target.value}),
        onBlur: () => save({ any_comment: c.any_comment }),
        style: { width: "100%", padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-8)", fontSize: 13, marginTop: 6, fontFamily: "inherit", resize: "vertical" },
      }),
    ),
  );

  const renderSearch = () => React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 12 } },
    // Sub-tabs
    React.createElement("div", { style: { display: "flex", gap: 4, borderBottom: "1px solid var(--line)" } },
      SUBTABS.map(st =>
        React.createElement("button", {
          key: st.k,
          onClick: () => setSubTab(st.k),
          style: {
            padding: "8px 14px",
            background: subTab === st.k ? "var(--signal)" : "transparent",
            color: subTab === st.k ? "var(--ink-0)" : "var(--ink-7)",
            border: 0, cursor: "pointer", fontSize: 12.5, fontWeight: 500,
            borderRadius: "4px 4px 0 0",
          },
        }, st.l)
      ),
    ),
    // Actions
    React.createElement("div", { style: { display: "flex", gap: 8, flexWrap: "wrap" } },
      React.createElement(Btn, {size: "s", kind: "ghost", onClick: loadFromAnalytics}, "⬇ Загрузить из аналитики"),
      React.createElement(Btn, {size: "s", kind: "ghost", onClick: loadFromMerchrules, title: "за последние 30 дней"}, "⬇ Из Merchrules (30 дней)"),
      React.createElement("label", {
        style: { padding: "6px 11px", fontSize: 12, border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-7)", cursor: "pointer" },
      },
        "📋 Импорт CSV",
        React.createElement("input", { type: "file", accept: ".csv,.txt", onChange: importCsv, style: { display: "none" } }),
      ),
      React.createElement(Btn, {size: "s", kind: "ghost", icon: React.createElement(I.plus, {size: 12}), onClick: addEmptyQuery}, "Добавить запрос"),
      React.createElement(Btn, {size: "s", kind: "primary", onClick: runCheckup, disabled: running},
        running ? "⏳ Проверка…" : "▶ Запустить"),
    ),
    // Table
    React.createElement("div", { style: { border: "1px solid var(--line)", borderRadius: 4, overflow: "auto", maxHeight: 450 } },
      React.createElement("table", { style: { width: "100%", borderCollapse: "collapse", fontSize: 12.5 } },
        React.createElement("thead", null,
          React.createElement("tr", { style: { background: "var(--ink-2)", position: "sticky", top: 0 } },
            ["#", "Запрос", "Показов", "Оценка", "Проблема", "Решение", "Комм.", "✕"].map((h, i) =>
              React.createElement("th", { key: i, style: { padding: "8px 10px", textAlign: "left", fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase", borderBottom: "1px solid var(--line)" } }, h)
            ),
          ),
        ),
        React.createElement("tbody", null,
          filtered.length === 0
            ? React.createElement("tr", null, React.createElement("td", { colSpan: 8, style: { padding: 28, textAlign: "center", color: "var(--ink-6)" } }, "Добавьте запросы для оценки"))
            : filtered.map((q, i) =>
              React.createElement("tr", { key: q.id, style: { borderBottom: "1px solid var(--line-soft)" } },
                React.createElement("td", { style: { padding: 6, color: "var(--ink-5)" } }, i + 1),
                React.createElement("td", { style: { padding: 6, fontWeight: 500 } }, q.query),
                React.createElement("td", { style: { padding: 6, color: "var(--ink-6)" } }, q.shows_count || "—"),
                React.createElement("td", { style: { padding: 6 } },
                  React.createElement("select", {
                    value: q.score == null ? "" : q.score,
                    onChange: e => updateQueryField(q, "score", e.target.value === "" ? null : parseInt(e.target.value)),
                    style: { background: "var(--ink-2)", border: "1px solid var(--line)", color: "var(--ink-8)", padding: 3 },
                  },
                    React.createElement("option", { value: "" }, "—"),
                    [0,1,2,3].map(n => React.createElement("option", { key: n, value: n }, n)),
                  ),
                ),
                React.createElement("td", { style: { padding: 6 } },
                  React.createElement("input", {
                    type: "text", value: q.problem || "",
                    onChange: e => setQueries(queries.map(x => x.id === q.id ? {...x, problem: e.target.value} : x)),
                    onBlur: e => updateQueryField(q, "problem", e.target.value),
                    style: { width: 120, background: "var(--ink-2)", border: "1px solid var(--line)", color: "var(--ink-8)", padding: 3 },
                  }),
                ),
                React.createElement("td", { style: { padding: 6 } },
                  React.createElement("input", {
                    type: "text", value: q.solution || "",
                    onChange: e => setQueries(queries.map(x => x.id === q.id ? {...x, solution: e.target.value} : x)),
                    onBlur: e => updateQueryField(q, "solution", e.target.value),
                    style: { width: 120, background: "var(--ink-2)", border: "1px solid var(--line)", color: "var(--ink-8)", padding: 3 },
                  }),
                ),
                React.createElement("td", { style: { padding: 6 } },
                  React.createElement("input", {
                    type: "text", value: q.partner_comment || "",
                    onChange: e => setQueries(queries.map(x => x.id === q.id ? {...x, partner_comment: e.target.value} : x)),
                    onBlur: e => updateQueryField(q, "partner_comment", e.target.value),
                    style: { width: 120, background: "var(--ink-2)", border: "1px solid var(--line)", color: "var(--ink-8)", padding: 3 },
                  }),
                ),
                React.createElement("td", { style: { padding: 6 } },
                  React.createElement("button", { onClick: () => deleteQuery(q.id), style: { background: "none", border: 0, color: "var(--critical)", cursor: "pointer" } }, "✕"),
                ),
              )
            ),
        ),
      ),
    ),
    React.createElement("div", null,
      React.createElement("label", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", textTransform: "uppercase" } }, "Комментарий по поиску"),
      React.createElement("textarea", {
        value: c.search_comment || "", rows: 3,
        onChange: e => setC({...c, search_comment: e.target.value}),
        onBlur: () => save({ search_comment: c.search_comment }),
        style: { width: "100%", padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-8)", fontSize: 13, marginTop: 6, fontFamily: "inherit", resize: "vertical" },
      }),
    ),
  );

  const renderPlaceholder = (sectionKey, title) => React.createElement("div", null,
    React.createElement("div", { style: { background: "var(--ink-2)", border: "1px dashed var(--line)", padding: 24, borderRadius: 4, color: "var(--ink-6)", fontSize: 12.5, textAlign: "center" } },
      title, " — раздел в разработке. Сохраните свободные заметки ниже."),
    React.createElement("textarea", {
      value: (c[sectionKey] && c[sectionKey].note) || "",
      rows: 6,
      onChange: e => setC({...c, [sectionKey]: {...(c[sectionKey]||{}), note: e.target.value}}),
      onBlur: () => save({ [sectionKey]: c[sectionKey] || {} }),
      placeholder: "Заметки по разделу…",
      style: { width: "100%", padding: "8px 10px", background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-8)", fontSize: 13, marginTop: 10, fontFamily: "inherit", resize: "vertical" },
    }),
  );

  const tabContent = [
    renderMain,
    renderSearch,
    () => renderPlaceholder("tracking", "Трекинг"),
    () => renderPlaceholder("uiux", "UI/UX"),
    () => renderPlaceholder("recs", "Рекомендации"),
    () => renderPlaceholder("reviews", "Отзывы"),
    () => renderPlaceholder("products_tab", "Продукты"),
    () => renderPlaceholder("debts", "Задолженность"),
  ][tab]();

  return React.createElement("div", {
    style: {
      position: "fixed", inset: 0, zIndex: 9998,
      background: "rgba(0,0,0,.55)", backdropFilter: "blur(3px)",
      display: "flex", alignItems: "flex-start", justifyContent: "center",
      padding: 24, overflowY: "auto",
    },
  },
    React.createElement("div", {
      style: {
        background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 8,
        width: "100%", maxWidth: 1000, boxShadow: "0 24px 64px rgba(0,0,0,.5)",
        display: "flex", flexDirection: "column", maxHeight: "calc(100vh - 48px)",
      },
    },
      // Header
      React.createElement("div", { style: { display: "flex", alignItems: "center", justifyContent: "space-between", padding: 18, borderBottom: "1px solid var(--line)" } },
        React.createElement("div", null,
          React.createElement("div", { style: { fontSize: 15, fontWeight: 600 } }, "Чекап"),
          React.createElement("div", { className: "mono", style: { fontSize: 11, color: "var(--ink-5)", marginTop: 2 } }, c.name || ""),
        ),
        React.createElement("button", { onClick: onClose, style: { background: "none", border: 0, color: "var(--ink-6)", fontSize: 20, cursor: "pointer" } }, "✕"),
      ),
      // Tab strip
      React.createElement("div", { style: { display: "flex", gap: 2, padding: "10px 18px", borderBottom: "1px solid var(--line-soft)", overflowX: "auto" } },
        TABS.map((t, i) =>
          React.createElement("button", {
            key: i, onClick: () => setTab(i),
            style: {
              padding: "6px 11px", fontSize: 11,
              background: i === tab ? "var(--signal)" : "var(--ink-2)",
              color: i === tab ? "var(--ink-0)" : "var(--ink-7)",
              border: 0, borderRadius: 4, cursor: "pointer", whiteSpace: "nowrap",
              fontFamily: "var(--f-mono)", textTransform: "uppercase", letterSpacing: "0.06em",
            },
          }, `${i+1}. ${t}`)
        ),
      ),
      // Body
      React.createElement("div", { style: { padding: 18, overflowY: "auto", flex: 1 } }, tabContent),
      // Footer
      React.createElement("div", { style: { padding: 14, borderTop: "1px solid var(--line)", display: "flex", justifyContent: "space-between", alignItems: "center" } },
        React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-6)" } },
          saving ? "Сохранение…" : `Статус: ${c.status}${c.score != null ? ` · балл ${c.score}/${c.score_max}` : ""}`),
        React.createElement("div", { style: { display: "flex", gap: 8 } },
          tab > 0 && React.createElement(Btn, { kind: "ghost", size: "m", onClick: () => setTab(tab - 1) }, "← Назад"),
          tab < TABS.length - 1 && React.createElement(Btn, { kind: "primary", size: "m", onClick: () => setTab(tab + 1) }, "Далее →"),
          React.createElement(Btn, { kind: "ghost", size: "m", onClick: onClose }, "Закрыть"),
        ),
      ),
    ),
  );
}
window.CheckupWizard = CheckupWizard;


// ── ClientProductsList — продукты клиента (из Airtable sync) ───────────────

function ClientProductsList({ clientId }) {
  const [list, setList] = React.useState(null);
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`/api/clients/${clientId}/products`, { credentials: "include" });
        if (!r.ok) { if (!cancelled) setList([]); return; }
        const d = await r.json();
        const arr = Array.isArray(d) ? d : (d.products || []);
        if (!cancelled) setList(arr);
      } catch (_) { if (!cancelled) setList([]); }
    })();
    return () => { cancelled = true; };
  }, [clientId]);
  const statusTone = { active: "ok", paused: "warn", trial: "info", disabled: "neutral" };
  if (list === null) return React.createElement(Card, { title: "Продукты" },
    React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "10px 0" } }, "Загрузка…"));
  if (!list.length) return React.createElement(Card, { title: "Продукты" },
    React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "10px 0" } },
      "Продуктов нет. Добавятся при синке Airtable (поле «Подключенные продукты»)."));
  return React.createElement(Card, { title: "Продукты" },
    list.map((p, i) => React.createElement("div", {
      key: p.id || i,
      style: {
        display: "flex", alignItems: "center", gap: 10, padding: "10px 0",
        borderBottom: i === list.length - 1 ? "none" : "1px solid var(--line-soft)",
      }
    },
      React.createElement("div", { style: { flex: 1, minWidth: 0 } },
        React.createElement("div", { style: { fontSize: 12.5, color: "var(--ink-8)" } }, p.name || p.code),
        p.code && React.createElement("div", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-5)" } }, p.code),
      ),
      React.createElement(Badge, { tone: statusTone[p.status] || "neutral", dot: true }, p.status || "—"),
    ))
  );
}
window.ClientProductsList = ClientProductsList;


// ── ClientFeedsList — фиды клиента (из ClientFeed) ─────────────────────────

function ClientFeedsList({ clientId }) {
  const [list, setList] = React.useState(null);
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`/api/clients/${clientId}/feeds`, { credentials: "include" });
        if (!r.ok) { if (!cancelled) setList([]); return; }
        const d = await r.json();
        const arr = Array.isArray(d) ? d : (d.feeds || []);
        if (!cancelled) setList(arr);
      } catch (_) { if (!cancelled) setList([]); }
    })();
    return () => { cancelled = true; };
  }, [clientId]);
  const statusTone = { ok: "ok", warning: "warn", error: "critical", disabled: "neutral" };
  if (list === null) return React.createElement(Card, { title: "Фиды" },
    React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "10px 0" } }, "Загрузка…"));
  if (!list.length) return React.createElement(Card, { title: "Фиды" },
    React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "10px 0" } },
      "Фидов нет. Добавятся когда синк фидов из Merchrules заработает."));
  return React.createElement(Card, { title: "Фиды" },
    list.map((f, i) => React.createElement("div", {
      key: f.id || i,
      style: {
        display: "flex", alignItems: "center", gap: 10, padding: "10px 0",
        borderBottom: i === list.length - 1 ? "none" : "1px solid var(--line-soft)",
      }
    },
      React.createElement("div", { style: { flex: 1, minWidth: 0 } },
        React.createElement("div", { style: { fontSize: 12.5, color: "var(--ink-8)" } },
          f.name || f.feed_type || "—"),
        React.createElement("div", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-5)" } },
          `${f.sku_count ? "SKU " + f.sku_count : ""}${f.errors_count ? " · err " + f.errors_count : ""}${f.last_updated ? " · " + f.last_updated.slice(0,10) : " · не проверялся"}`.replace(/^ · /, "")),
      ),
      React.createElement(Badge, { tone: statusTone[f.status] || "neutral", dot: true }, f.status || "—"),
    ))
  );
}
window.ClientFeedsList = ClientFeedsList;


// ── ClientOnboardingCard — 10-step per-client onboarding (кнопка + прогресс + модал) ──
function ClientOnboardingCard({ clientId }) {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [modalOpen, setModalOpen] = React.useState(false);

  const load = React.useCallback(() => {
    setLoading(true);
    fetch(`/api/clients/${clientId}/onboarding`, { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => setData(d))
      .finally(() => setLoading(false));
  }, [clientId]);

  React.useEffect(() => { load(); }, [load]);

  const start = () => {
    fetch(`/api/clients/${clientId}/onboarding/start`, {
      method: "POST", credentials: "include",
    }).then(() => load());
  };

  const markSent = () => {
    fetch(`/api/clients/${clientId}/onboarding/mark-sent`, {
      method: "POST", credentials: "include",
    }).then(() => { setModalOpen(false); load(); });
  };

  if (loading) {
    return React.createElement(Card, { title: "Онбординг" },
      React.createElement("div", { style: { color: "var(--ink-5)", fontSize: 12.5 } }, "Загружаем…"));
  }

  if (!data || !data.active && !data.completed_at) {
    return React.createElement(Card, { title: "Онбординг" },
      React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 12 } },
        React.createElement("div", { style: { flex: 1, fontSize: 12.5, color: "var(--ink-6)" } },
          "Онбординг ещё не запущен. 10 сообщений × 5 недель, по 2 в неделю."),
        React.createElement(Btn, { kind: "primary", size: "s", onClick: start }, "Запустить онбординг"),
      )
    );
  }

  if (data.completed_at) {
    return React.createElement(Card, { title: "Онбординг" },
      React.createElement(Badge, { tone: "ok", dot: true }, "Онбординг завершён · 10/10"),
      React.createElement("div", { style: { marginTop: 8, fontSize: 12, color: "var(--ink-5)" } },
        "Завершён " + (data.completed_at ? data.completed_at.slice(0, 10) : "")));
  }

  const step = data.current_step || 0;
  const pct = Math.round(step / 10 * 100);
  const tpl = data.current_template;

  return React.createElement(Card, { title: "Онбординг · " + step + "/10" },
    React.createElement("div", {
      style: {
        height: 6, background: "var(--ink-2)", borderRadius: 4, overflow: "hidden",
        marginBottom: 12,
      }
    },
      React.createElement("div", { style: { width: pct + "%", height: "100%", background: "var(--signal)" } }),
    ),
    React.createElement("div", { style: { fontSize: 12, color: "var(--ink-6)", marginBottom: 10 } },
      data.next_step
        ? `Следующее сообщение #${data.next_step}${data.next_send_date ? " · план: " + data.next_send_date : ""}`
        : "Все 10 сообщений отправлены."),
    tpl && React.createElement("div", null,
      React.createElement("div", { style: { fontSize: 13, color: "var(--ink-8)", marginBottom: 8 } }, tpl.title),
      React.createElement(Btn, { kind: "primary", size: "s", onClick: () => setModalOpen(true) },
        data.open_task_id ? "Открыть текст · " + tpl.step : "Показать текст шага " + tpl.step),
    ),
    modalOpen && tpl && React.createElement("div", {
      style: {
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 100,
        display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
      },
      onClick: (e) => { if (e.target === e.currentTarget) setModalOpen(false); },
    },
      React.createElement("div", {
        style: {
          background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 8,
          maxWidth: 640, width: "100%", padding: 24, maxHeight: "85vh", overflow: "auto",
        }
      },
        React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 10, marginBottom: 12 } },
          React.createElement(Badge, { tone: "signal" }, "Шаг " + tpl.step + " / 10"),
          React.createElement("h3", { style: { margin: 0, fontSize: 17, color: "var(--ink-9)" } }, tpl.title),
        ),
        React.createElement("div", {
          style: {
            padding: 14, background: "var(--ink-2)", border: "1px solid var(--line-soft)",
            borderRadius: 6, fontSize: 13, lineHeight: 1.55, whiteSpace: "pre-wrap",
            color: "var(--ink-8)", marginBottom: 16,
          }
        }, tpl.body),
        React.createElement("div", { style: { display: "flex", gap: 8, justifyContent: "flex-end" } },
          React.createElement(Btn, {
            kind: "ghost", size: "m",
            onClick: () => { navigator.clipboard && navigator.clipboard.writeText(tpl.body); }
          }, "Копировать"),
          React.createElement(Btn, { kind: "ghost", size: "m", onClick: () => setModalOpen(false) }, "Закрыть"),
          React.createElement(Btn, { kind: "primary", size: "m", onClick: markSent }, "Отправлено в TG"),
        ),
      )
    )
  );
}
window.ClientOnboardingCard = ClientOnboardingCard;


// ── PageManagerGroups — admin CRUD групп менеджеров ────────────────────────
function PageManagerGroups() {
  const [groups, setGroups] = React.useState([]);
  const [users, setUsers] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [newName, setNewName] = React.useState("");

  const load = React.useCallback(() => {
    Promise.all([
      fetch("/api/admin/groups", { credentials: "include" }).then(r => r.ok ? r.json() : []),
      fetch("/api/admin/users",  { credentials: "include" }).then(r => r.ok ? r.json() : []),
    ]).then(([g, u]) => {
      setGroups(Array.isArray(g) ? g : []);
      setUsers(Array.isArray(u) ? u : (u.users || []));
      setLoading(false);
    });
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const createGroup = () => {
    const n = newName.trim();
    if (!n) return;
    fetch("/api/admin/groups", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: n }),
    }).then(() => { setNewName(""); load(); });
  };

  const removeGroup = (id) => {
    if (!window.confirm("Удалить группу? Менеджеры будут отвязаны.")) return;
    fetch(`/api/admin/groups/${id}`, { method: "DELETE", credentials: "include" })
      .then(load);
  };

  const setUserGroup = (userId, groupId, role) => {
    fetch(`/api/admin/users/${userId}/group`, {
      method: "PUT", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group_id: groupId || null, role }),
    }).then(load);
  };

  const setGroupHead = (gid, userId) => {
    fetch(`/api/admin/groups/${gid}`, {
      method: "PUT", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ grouphead_id: userId || null }),
    }).then(load);
  };

  return React.createElement(React.Fragment, null,
    React.createElement(TopBar, {
      breadcrumbs: ["am hub", "админ", "группы"],
      title: "Группы менеджеров",
      subtitle: "grouphead видит свою группу, leadership переключает scope",
    }),
    React.createElement("div", { style: { padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 } },

      React.createElement(Card, { title: "Новая группа" },
        React.createElement("div", { style: { display: "flex", gap: 8 } },
          React.createElement("input", {
            value: newName, onChange: e => setNewName(e.target.value),
            placeholder: "Например: Команда Альфа",
            style: {
              flex: 1, background: "var(--ink-2)", border: "1px solid var(--line)",
              borderRadius: 4, padding: "8px 12px", color: "var(--ink-8)", fontSize: 13,
            },
            onKeyDown: e => { if (e.key === "Enter") createGroup(); },
          }),
          React.createElement(Btn, { kind: "primary", size: "m", onClick: createGroup }, "Создать"),
        ),
      ),

      loading
        ? React.createElement(Card, { title: "Группы" }, "Загружаем…")
        : groups.length === 0
          ? React.createElement(Card, { title: "Группы" }, "Пока нет групп.")
          : groups.map(g => React.createElement(Card, {
              key: g.id,
              title: g.name,
              actions: React.createElement(Btn, { kind: "ghost", size: "s",
                onClick: () => removeGroup(g.id) }, "Удалить"),
            },
              React.createElement("div", { style: { marginBottom: 12, fontSize: 12, color: "var(--ink-6)" } },
                "Руководитель группы (grouphead):",
              ),
              React.createElement("select", {
                value: g.grouphead_id || "",
                onChange: e => setGroupHead(g.id, e.target.value ? Number(e.target.value) : null),
                style: {
                  width: "100%", marginBottom: 14, padding: "6px 10px", fontSize: 12.5,
                  background: "var(--ink-2)", border: "1px solid var(--line)",
                  borderRadius: 4, color: "var(--ink-8)",
                },
              },
                React.createElement("option", { value: "" }, "— не назначен —"),
                users.filter(u => ["admin","grouphead","manager"].includes(u.role))
                     .map(u => React.createElement("option", { key: u.id, value: u.id },
                       `${u.email} (${u.role})`)),
              ),
              React.createElement("div", { style: { fontSize: 12, color: "var(--ink-6)", marginBottom: 6 } },
                `Менеджеры в группе (${g.members ? g.members.length : 0}):`),
              React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 6 } },
                (g.members || []).map(m => React.createElement("div", {
                  key: m.id,
                  style: {
                    display: "flex", gap: 10, alignItems: "center",
                    padding: "6px 10px", background: "var(--ink-2)",
                    border: "1px solid var(--line-soft)", borderRadius: 4,
                  },
                },
                  React.createElement("span", { style: { flex: 1, fontSize: 12.5, color: "var(--ink-8)" } }, m.email),
                  React.createElement(Badge, { tone: "neutral" }, m.role),
                  React.createElement(Btn, {
                    kind: "ghost", size: "s",
                    onClick: () => setUserGroup(m.id, null, m.role),
                  }, "Убрать"),
                )),
              ),
            )),

      React.createElement(Card, { title: "Все менеджеры" },
        users.length === 0 ? "Нет пользователей" :
          React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 4 } },
            users.map(u => React.createElement("div", {
              key: u.id,
              style: {
                display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr",
                gap: 10, padding: "6px 0", alignItems: "center",
                borderBottom: "1px solid var(--line-soft)", fontSize: 12.5,
              },
            },
              React.createElement("span", { style: { color: "var(--ink-8)" } }, u.email),
              React.createElement("select", {
                value: u.role || "manager",
                onChange: e => setUserGroup(u.id, u.group_id, e.target.value),
                style: {
                  padding: "4px 8px", fontSize: 11,
                  background: "var(--ink-2)", border: "1px solid var(--line)",
                  borderRadius: 4, color: "var(--ink-8)",
                },
              },
                ["admin","grouphead","leadership","manager","viewer"].map(r =>
                  React.createElement("option", { key: r, value: r }, r)),
              ),
              React.createElement("select", {
                value: u.group_id || "",
                onChange: e => setUserGroup(u.id, e.target.value ? Number(e.target.value) : null, u.role),
                style: {
                  padding: "4px 8px", fontSize: 11,
                  background: "var(--ink-2)", border: "1px solid var(--line)",
                  borderRadius: 4, color: "var(--ink-8)",
                },
              },
                React.createElement("option", { value: "" }, "— без группы —"),
                groups.map(g => React.createElement("option", { key: g.id, value: g.id }, g.name)),
              ),
              React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-5)" } },
                u.is_active ? "active" : "disabled"),
            )),
          ),
      ),
    )
  );
}
window.PageManagerGroups = PageManagerGroups;


// ── ClientMerchrulesDashboard — синонимы/white/black/merch-rules из дашборда Merchrules ──
function ClientMerchrulesDashboard({ clientId }) {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [syncing, setSyncing] = React.useState(false);
  const [tab, setTab] = React.useState("synonyms"); // synonyms | whitelist | blacklist | merch_rules

  const load = React.useCallback(() => {
    setLoading(true);
    fetch(`/api/clients/${clientId}/merchrules-dashboard`, { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [clientId]);
  React.useEffect(() => { load(); }, [load]);

  const runSync = async () => {
    setSyncing(true);
    try {
      const r = await fetch(`/api/clients/${clientId}/merchrules-dashboard/sync`, {
        method: "POST", credentials: "include",
      });
      const j = await r.json().catch(() => ({}));
      if (r.ok && j.ok) {
        window.appToast && window.appToast(
          `Синк: ${j.counts.synonyms || 0} син / ${j.counts.whitelist || 0} wl / ${j.counts.blacklist || 0} bl / ${j.counts.merch_rules || 0} rules`
        );
        load();
      } else {
        window.appToast && window.appToast(j.detail || j.error || "Ошибка синка");
      }
    } catch (e) {
      window.appToast && window.appToast("Сеть/500");
    }
    setSyncing(false);
  };

  const counts = data ? {
    synonyms: (data.synonyms || []).length,
    whitelist: (data.whitelist || []).length,
    blacklist: (data.blacklist || []).length,
    merch_rules: (data.merch_rules || []).length,
  } : { synonyms: 0, whitelist: 0, blacklist: 0, merch_rules: 0 };

  const tabs = [
    { k: "synonyms",    l: "Синонимы",   c: counts.synonyms },
    { k: "whitelist",   l: "Whitelist",  c: counts.whitelist },
    { k: "blacklist",   l: "Blacklist",  c: counts.blacklist },
    { k: "merch_rules", l: "Правила",    c: counts.merch_rules },
  ];

  const items = (data && data[tab]) || [];

  const lastSynced = data && ["synonyms","whitelist","blacklist","merch_rules"]
    .flatMap(k => (data[k] || []).map(x => x.last_synced))
    .filter(Boolean)
    .sort()
    .pop();

  return React.createElement(Card, {
    title: "Merchrules · дашборд",
    actions: React.createElement(Btn, {
      kind: "ghost", size: "s",
      onClick: runSync, disabled: syncing,
    }, syncing ? "Синк…" : "↻ Синк"),
  },
    React.createElement("div", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-5)", marginBottom: 10 } },
      lastSynced ? `обновлено ${lastSynced.slice(0,16).replace("T"," ")}` : "ещё не синкалось"),

    React.createElement("div", { style: { display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap" } },
      tabs.map(t => React.createElement("button", {
        key: t.k,
        onClick: () => setTab(t.k),
        style: {
          padding: "4px 10px", borderRadius: 4,
          background: tab === t.k ? "var(--signal)" : "var(--ink-2)",
          color: tab === t.k ? "var(--ink-0)" : "var(--ink-7)",
          border: `1px solid ${tab === t.k ? "var(--signal)" : "var(--line)"}`,
          cursor: "pointer", fontSize: 11,
          fontFamily: "var(--f-mono)", textTransform: "uppercase",
          letterSpacing: "0.06em",
        },
      }, `${t.l} · ${t.c}`)),
    ),

    loading
      ? React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5 } }, "Загружаем…")
      : items.length === 0
        ? React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "10px 0" } },
            "Пусто. Нажми «↻ Синк» чтобы подтянуть из Merchrules.")
        : React.createElement("div", { style: { display: "flex", flexDirection: "column", maxHeight: 320, overflowY: "auto" } },
            items.map((it, i) => {
              let primary, secondary;
              if (tab === "synonyms") {
                primary = it.term;
                secondary = (it.synonyms || []).join(", ") || "—";
              } else if (tab === "whitelist" || tab === "blacklist") {
                primary = it.query;
                secondary = [it.product_name, it.product_id && "#" + it.product_id, it.position && "pos " + it.position]
                  .filter(Boolean).join(" · ") || "—";
              } else {
                primary = it.name;
                secondary = [it.rule_type, "prio " + (it.priority || 0), it.status]
                  .filter(Boolean).join(" · ");
              }
              return React.createElement("div", {
                key: it.id || i,
                style: {
                  display: "grid", gridTemplateColumns: "1fr 60px",
                  gap: 10, padding: "8px 0",
                  borderBottom: i === items.length - 1 ? "none" : "1px solid var(--line-soft)",
                  alignItems: "center",
                },
              },
                React.createElement("div", { style: { minWidth: 0 } },
                  React.createElement("div", { style: { fontSize: 12.5, color: "var(--ink-8)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, primary),
                  React.createElement("div", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-5)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, secondary),
                ),
                React.createElement(Badge, {
                  tone: it.is_active === false ? "neutral" : (tab === "blacklist" ? "critical" : "ok"),
                  dot: true,
                }, it.is_active === false ? "off" : "on"),
              );
            }),
          ),
  );
}
window.ClientMerchrulesDashboard = ClientMerchrulesDashboard;


// ── ClientVoiceNotes — запись в браузере + upload + плеер + транскрипция ────
function ClientVoiceNotes({ clientId }) {
  const [notes, setNotes] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [recording, setRecording] = React.useState(false);
  const [uploading, setUploading] = React.useState(false);
  const [err, setErr] = React.useState(null);
  const recorderRef = React.useRef(null);
  const chunksRef = React.useRef([]);

  const load = React.useCallback(() => {
    setLoading(true);
    fetch(`/api/clients/${clientId}/voice-notes`, { credentials: "include" })
      .then(r => r.ok ? r.json() : { items: [] })
      .then(d => { setNotes(d.items || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, [clientId]);
  React.useEffect(() => { load(); }, [load]);

  async function startRecord() {
    setErr(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm"
                 : MediaRecorder.isTypeSupported("audio/ogg") ? "audio/ogg" : "";
      const rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data); };
      rec.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
        await uploadBlob(blob);
      };
      rec.start();
      recorderRef.current = rec;
      setRecording(true);
    } catch (e) {
      setErr("Нет доступа к микрофону: " + (e.message || e));
    }
  }

  function stopRecord() {
    try { recorderRef.current && recorderRef.current.stop(); } catch (e) {}
    setRecording(false);
  }

  async function uploadBlob(blob) {
    setUploading(true); setErr(null);
    try {
      const ext = (blob.type.split("/")[1] || "webm").split(";")[0];
      const fd = new FormData();
      fd.append("client_id", String(clientId));
      fd.append("audio", blob, `voice.${ext}`);
      const r = await fetch("/api/voice-notes", {
        method: "POST", credentials: "include", body: fd,
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      load();
      window.appToast && window.appToast("✓ Голосовая заметка сохранена");
    } catch (e) {
      setErr(e.message || "upload failed");
    } finally { setUploading(false); }
  }

  async function retranscribe(id) {
    const r = await fetch(`/api/voice-notes/${id}/transcribe`, {
      method: "POST", credentials: "include",
    });
    if (r.ok) { load(); window.appToast && window.appToast("✓ Перетранскрибировано"); }
    else { window.appToast && window.appToast("Не удалось: HTTP " + r.status); }
  }

  async function removeNote(id) {
    if (!window.confirm("Удалить заметку?")) return;
    const r = await fetch(`/api/voice-notes/${id}`, {
      method: "DELETE", credentials: "include",
    });
    if (r.ok) load();
  }

  return React.createElement(Card, {
    title: "Голосовые заметки" + (notes.length ? ` · ${notes.length}` : ""),
    actions: recording
      ? React.createElement(Btn, { kind: "critical", size: "s", onClick: stopRecord }, "⏹ Стоп")
      : React.createElement(Btn, { kind: "primary", size: "s", onClick: startRecord, disabled: uploading },
          uploading ? "Загружаем…" : "🎙 Записать"),
  },
    err && React.createElement("div", { style: { fontSize: 11.5, color: "var(--critical)", marginBottom: 8 } }, err),

    loading
      ? React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5 } }, "Загружаем…")
      : notes.length === 0
        ? React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12.5, padding: "10px 0" } },
            "Пока нет заметок. Жми «Записать» или отправь voice в TG-бота.")
        : React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 10, maxHeight: 360, overflowY: "auto" } },
            notes.map((n) => React.createElement("div", {
              key: n.id,
              style: {
                padding: "10px 12px", background: "var(--ink-2)",
                border: "1px solid var(--line-soft)", borderRadius: 6,
                display: "flex", flexDirection: "column", gap: 6,
              },
            },
              React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 8 } },
                n.audio_url && React.createElement("audio", {
                  src: n.audio_url, controls: true,
                  style: { height: 28, flex: 1 },
                }),
                React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-5)" } },
                  n.created_at ? n.created_at.slice(0, 16).replace("T", " ") : ""),
                React.createElement("button", {
                  onClick: () => retranscribe(n.id),
                  title: "Перетранскрибировать",
                  style: { background: "transparent", border: "1px solid var(--line)", borderRadius: 3, color: "var(--ink-6)", cursor: "pointer", padding: "2px 6px", fontSize: 11 },
                }, "🔁"),
                React.createElement("button", {
                  onClick: () => removeNote(n.id),
                  title: "Удалить",
                  style: { background: "transparent", border: "1px solid var(--line)", borderRadius: 3, color: "var(--ink-6)", cursor: "pointer", padding: "2px 6px", fontSize: 11 },
                }, "✕"),
              ),
              React.createElement("div", {
                style: {
                  fontSize: 12.5, color: n.transcription ? "var(--ink-8)" : "var(--ink-5)",
                  fontStyle: n.transcription ? "normal" : "italic",
                  lineHeight: 1.5, whiteSpace: "pre-wrap",
                },
              }, n.transcription || "Транскрипция не готова (нужен GROQ_API_KEY)."),
            )),
          ),
  );
}
window.ClientVoiceNotes = ClientVoiceNotes;
// ── ClientTransferBanner + Modal ────────────────────────────────────────────
// На карточке клиента: если есть pending входящий запрос — баннер с accept/decline.
// Если текущий manager и нет pending — кнопка «Передать клиента» → модалка.
function ClientTransferSection({ client, currentUser, onRefresh }) {
  const [pending, setPending] = React.useState(null);
  const [users, setUsers] = React.useState([]);
  const [modalOpen, setModalOpen] = React.useState(false);

  const load = React.useCallback(() => {
    fetch(`/api/clients/${client.id}/transfer`, { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => setPending(d || null))
      .catch(() => {});
  }, [client.id]);
  React.useEffect(() => { load(); }, [load]);

  React.useEffect(() => {
    fetch("/api/admin/users", { credentials: "include" })
      .then(r => r.ok ? r.json() : [])
      .then(d => setUsers(Array.isArray(d) ? d : (d.users || [])))
      .catch(() => {});
  }, []);

  const isIncoming = pending && currentUser && pending.to_email === currentUser.email;
  const isOutgoing = pending && currentUser && pending.from_email === currentUser.email;
  const isOwner = currentUser && client.manager_email === currentUser.email;

  const accept = async () => {
    if (!window.confirm("Принять передачу клиента?")) return;
    const r = await fetch(`/api/transfers/${pending.id}/accept`, {
      method: "POST", credentials: "include",
    });
    if (r.ok) {
      const d = await r.json();
      window.appToast && window.appToast(`✓ Клиент принят. Задач переназначено: ${d.tasks_reassigned}`);
      load();
      onRefresh && onRefresh();
    }
  };
  const decline = async () => {
    const reason = window.prompt("Причина отказа (опционально):", "") || "";
    const r = await fetch(`/api/transfers/${pending.id}/decline`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    });
    if (r.ok) { window.appToast && window.appToast("Передача отклонена"); load(); }
  };
  const cancel = async () => {
    if (!window.confirm("Отозвать запрос на передачу?")) return;
    const r = await fetch(`/api/transfers/${pending.id}/cancel`, {
      method: "POST", credentials: "include",
    });
    if (r.ok) { window.appToast && window.appToast("Запрос отозван"); load(); }
  };

  // Incoming pending → баннер
  if (isIncoming) {
    return React.createElement("div", {
      style: {
        padding: 14, background: "color-mix(in oklch, var(--signal) 8%, var(--ink-2))",
        border: "1px solid var(--signal)", borderRadius: 6, marginBottom: 12,
      },
    },
      React.createElement("div", { style: { fontSize: 13, fontWeight: 600, color: "var(--ink-9)", marginBottom: 6 } },
        "🤝 Входящий запрос на передачу клиента от " + (pending.from_email || "—")),
      pending.manual_note && React.createElement("div", { style: { fontSize: 12, color: "var(--ink-7)", marginBottom: 6 } },
        "Заметка: " + pending.manual_note),
      pending.ai_summary && React.createElement("details", { style: { marginBottom: 10 } },
        React.createElement("summary", { style: { fontSize: 12, color: "var(--ink-7)", cursor: "pointer" } }, "AI-сводка"),
        React.createElement("pre", {
          style: { fontSize: 12, color: "var(--ink-8)", whiteSpace: "pre-wrap", marginTop: 6, fontFamily: "inherit", lineHeight: 1.5 },
        }, pending.ai_summary),
      ),
      React.createElement("div", { style: { display: "flex", gap: 8 } },
        React.createElement(Btn, { kind: "primary", size: "s", onClick: accept }, "✓ Принять"),
        React.createElement(Btn, { kind: "ghost", size: "s", onClick: decline }, "✕ Отклонить"),
      ),
    );
  }

  // Outgoing pending → статус + отозвать
  if (isOutgoing) {
    return React.createElement("div", {
      style: {
        padding: 12, background: "var(--ink-2)",
        border: "1px dashed var(--warn)", borderRadius: 6, marginBottom: 12,
      },
    },
      React.createElement("div", { style: { fontSize: 12.5, color: "var(--ink-8)", marginBottom: 6 } },
        `⏳ Ожидает подтверждения от ${pending.to_email}`),
      React.createElement(Btn, { kind: "ghost", size: "s", onClick: cancel }, "Отозвать"),
    );
  }

  // Не owner и нет pending → ничего
  if (!isOwner) return null;

  return React.createElement(React.Fragment, null,
    React.createElement(Btn, {
      kind: "ghost", size: "s",
      onClick: () => setModalOpen(true),
    }, "🤝 Передать клиента"),
    modalOpen && React.createElement(ClientTransferModal, {
      client, users, onClose: () => setModalOpen(false),
      onDone: () => { setModalOpen(false); load(); },
    }),
  );
}

function ClientTransferModal({ client, users, onClose, onDone }) {
  const [toUserId, setToUserId] = React.useState("");
  const [manualNote, setManualNote] = React.useState("");
  const [aiSummary, setAiSummary] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [requestId, setRequestId] = React.useState(null);
  const [err, setErr] = React.useState(null);

  const eligible = users.filter(u =>
    ["manager", "grouphead", "admin"].includes(u.role) &&
    u.email !== (client.manager_email || ""));

  const generate = async () => {
    if (!toUserId) { setErr("Выбери менеджера"); return; }
    setLoading(true); setErr(null);
    try {
      const r = await fetch(`/api/clients/${client.id}/transfer`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          to_user_id: Number(toUserId),
          manual_note: manualNote,
        }),
      });
      const d = await r.json();
      if (!r.ok) { setErr(d.detail || "HTTP " + r.status); return; }
      setRequestId(d.id);
      setAiSummary(d.ai_summary || "");
    } catch (e) { setErr(e.message); }
    finally { setLoading(false); }
  };

  const saveEdit = async () => {
    if (!requestId) return;
    const r = await fetch(`/api/transfers/${requestId}`, {
      method: "PATCH", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ai_summary: aiSummary, manual_note: manualNote }),
    });
    if (r.ok) {
      window.appToast && window.appToast("✓ Запрос отправлен новому менеджеру");
      onDone && onDone();
    } else {
      setErr("Не удалось обновить");
    }
  };

  return React.createElement("div", {
    onClick: (e) => { if (e.target === e.currentTarget) onClose(); },
    style: {
      position: "fixed", inset: 0, zIndex: 9998,
      background: "rgba(0,0,0,.55)", backdropFilter: "blur(3px)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
    },
  },
    React.createElement("div", {
      style: {
        background: "var(--ink-1)", border: "1px solid var(--line)",
        borderRadius: 10, maxWidth: 720, width: "100%",
        maxHeight: "88vh", display: "flex", flexDirection: "column",
        padding: 20, boxShadow: "0 24px 64px rgba(0,0,0,.5)",
      },
    },
      React.createElement("div", { style: { display: "flex", justifyContent: "space-between", marginBottom: 14 } },
        React.createElement("div", { style: { fontSize: 16, fontWeight: 600 } }, "🤝 Передать клиента " + client.name),
        React.createElement("button", { onClick: onClose, style: { background: "none", border: 0, color: "var(--ink-5)", cursor: "pointer", fontSize: 20 } }, "×"),
      ),

      err && React.createElement("div", { style: { fontSize: 12, color: "var(--critical)", marginBottom: 10 } }, err),

      !requestId && React.createElement("div", null,
        React.createElement("div", { style: { fontSize: 12, color: "var(--ink-6)", marginBottom: 4 } }, "Кому передаём:"),
        React.createElement("select", {
          value: toUserId, onChange: e => setToUserId(e.target.value),
          style: {
            width: "100%", padding: "8px 10px", marginBottom: 12,
            background: "var(--ink-2)", border: "1px solid var(--line)",
            borderRadius: 4, color: "var(--ink-8)",
          },
        },
          React.createElement("option", { value: "" }, "— выбери менеджера —"),
          eligible.map(u => React.createElement("option", { key: u.id, value: u.id },
            `${u.email} (${u.role})`)),
        ),
        React.createElement("div", { style: { fontSize: 12, color: "var(--ink-6)", marginBottom: 4 } }, "Заметка для нового менеджера (опционально):"),
        React.createElement("textarea", {
          value: manualNote, onChange: e => setManualNote(e.target.value), rows: 3,
          placeholder: "Например: «клиент сейчас в активной фазе upsell, хочет интеграцию с X»",
          style: {
            width: "100%", resize: "vertical", padding: "8px 10px", marginBottom: 14,
            background: "var(--ink-2)", border: "1px solid var(--line)",
            borderRadius: 4, color: "var(--ink-8)", fontSize: 12.5, fontFamily: "inherit",
          },
        }),
        React.createElement("div", { style: { display: "flex", justifyContent: "flex-end", gap: 8 } },
          React.createElement(Btn, { kind: "ghost", size: "m", onClick: onClose }, "Отмена"),
          React.createElement(Btn, { kind: "primary", size: "m", onClick: generate, disabled: loading || !toUserId },
            loading ? "AI думает…" : "Сгенерировать сводку"),
        ),
      ),

      requestId && React.createElement("div", null,
        React.createElement("div", { style: { fontSize: 12, color: "var(--ink-6)", marginBottom: 4 } }, "AI-сводка (можно править):"),
        React.createElement("textarea", {
          value: aiSummary, onChange: e => setAiSummary(e.target.value), rows: 18,
          style: {
            width: "100%", resize: "vertical", padding: "10px 12px", marginBottom: 14,
            background: "var(--ink-2)", border: "1px solid var(--line)",
            borderRadius: 4, color: "var(--ink-8)", fontSize: 12.5, lineHeight: 1.5,
            fontFamily: "inherit", whiteSpace: "pre-wrap",
          },
        }),
        React.createElement("div", { style: { display: "flex", justifyContent: "flex-end", gap: 8 } },
          React.createElement(Btn, { kind: "ghost", size: "m", onClick: onClose }, "Отмена"),
          React.createElement(Btn, { kind: "primary", size: "m", onClick: saveEdit }, "📤 Отправить запрос"),
        ),
      ),
    ),
  );
}

window.ClientTransferSection = ClientTransferSection;
window.ClientTransferModal = ClientTransferModal;


// ── PageRenewal — Kanban по срокам истечения контракта ─────────────────────
function PageRenewal() {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    fetch("/api/me/renewal-pipeline", { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const mrrFmt = (v) => {
    if (v == null) return "—";
    if (v >= 1_000_000) return `₽ ${(v/1_000_000).toFixed(1)}м`;
    if (v >= 1_000) return `₽ ${Math.round(v/1000)}к`;
    return `₽ ${Math.round(v)}`;
  };

  const order = ["overdue", "critical", "week", "month", "quarter", "later"];

  return React.createElement(React.Fragment, null,
    React.createElement(TopBar, {
      breadcrumbs: ["am hub", "клиенты", "renewal"],
      title: "Renewal pipeline",
      subtitle: loading ? "…" :
        (data ? `${data.total_clients} клиентов · MRR ${mrrFmt(data.total_mrr)}` : "Нет данных"),
    }),
    React.createElement("div", { style: { padding: "22px 28px 40px" } },
      loading
        ? React.createElement("div", { style: { color: "var(--ink-6)" } }, "Загружаем…")
        : !data || data.total_clients === 0
          ? React.createElement("div", { style: { color: "var(--ink-6)", padding: "40px 0", textAlign: "center" } },
              "Нет клиентов с заполненным полем contract_end. Добавь дату окончания контракта в карточке клиента.")
          : React.createElement("div", {
              style: { display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 12, alignItems: "start" },
            },
              order.map(k => {
                const col = data.columns[k];
                return React.createElement("div", {
                  key: k,
                  style: {
                    background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 6,
                    padding: 10, minHeight: 200,
                  },
                },
                  React.createElement("div", {
                    style: { display: "flex", justifyContent: "space-between", marginBottom: 10, alignItems: "center" },
                  },
                    React.createElement("div", { style: { fontSize: 11.5, fontWeight: 500, color: `var(--${col.tone})`, textTransform: "uppercase", letterSpacing: "0.06em" } },
                      col.label),
                    React.createElement("span", { className: "mono", style: { fontSize: 11, color: "var(--ink-6)" } },
                      String(col.items.length)),
                  ),
                  col.items.length === 0
                    ? React.createElement("div", { style: { color: "var(--ink-5)", fontSize: 11, padding: "12px 0", textAlign: "center" } }, "—")
                    : React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 6 } },
                        col.items.map(it => React.createElement("a", {
                          key: it.id,
                          href: `/design/client/${it.id}`,
                          style: {
                            padding: 10, background: "var(--ink-1)", border: "1px solid var(--line-soft)",
                            borderRadius: 4, fontSize: 12, color: "var(--ink-8)",
                            textDecoration: "none", display: "block",
                          },
                        },
                          React.createElement("div", { style: { fontWeight: 500, marginBottom: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } },
                            it.name),
                          React.createElement("div", { className: "mono", style: { fontSize: 10, color: "var(--ink-6)" } },
                            `${it.segment || "—"} · MRR ${mrrFmt(it.mrr)}`),
                          React.createElement("div", { className: "mono", style: { fontSize: 10, color: "var(--ink-5)", marginTop: 2 } },
                            `${it.contract_end} · ${it.days_left < 0 ? `просрочено ${-it.days_left}д` : it.days_left + ' дн'} · health ${it.health}%`),
                        )),
                      ),
                );
              }),
            ),
    ),
  );
}
window.PageRenewal = PageRenewal;


// ── ClientUpsellCard — активное предложение апсейла на странице клиента ────
function ClientUpsellCard({ clientId }) {
  const [events, setEvents] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [adding, setAdding] = React.useState(false);
  const [form, setForm] = React.useState({ event_type: "upsell", description: "", amount_after: "" });

  const load = React.useCallback(() => {
    setLoading(true);
    fetch(`/api/clients/${clientId}/upsell`, { credentials: "include" })
      .then(r => r.ok ? r.json() : [])
      .then(d => { setEvents(Array.isArray(d) ? d : (d.items || [])); setLoading(false); })
      .catch(() => setLoading(false));
  }, [clientId]);
  React.useEffect(() => { load(); }, [load]);

  const create = async () => {
    const payload = {
      client_id: clientId,
      event_type: form.event_type,
      description: form.description.trim(),
      amount_after: form.amount_after ? Number(form.amount_after) : null,
    };
    const r = await fetch("/api/upsell/event", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (r.ok) {
      setAdding(false); setForm({ event_type: "upsell", description: "", amount_after: "" });
      load();
    }
  };

  const updateStatus = async (id, status) => {
    const r = await fetch(`/api/upsell/event/${id}`, {
      method: "PATCH", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    if (r.ok) load();
  };

  const active = (events || []).filter(e => !["won", "lost"].includes(e.status));
  const closed = (events || []).filter(e => ["won", "lost"].includes(e.status));

  const statusColor = {
    identified: "info", in_progress: "warn", won: "ok", lost: "critical", postponed: "neutral",
  };

  return React.createElement(Card, {
    title: "Upsell" + (active.length ? ` · ${active.length}` : ""),
    actions: React.createElement(Btn, {
      kind: "ghost", size: "s", onClick: () => setAdding(v => !v),
    }, adding ? "Отмена" : "+ Новый"),
  },
    adding && React.createElement("div", {
      style: { padding: 10, background: "var(--ink-2)", borderRadius: 4, marginBottom: 10, display: "flex", flexDirection: "column", gap: 6 },
    },
      React.createElement("select", {
        value: form.event_type, onChange: e => setForm(f => ({ ...f, event_type: e.target.value })),
        style: { padding: "6px 8px", background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 3, color: "var(--ink-8)", fontSize: 12 },
      },
        React.createElement("option", { value: "upsell" }, "Upsell"),
        React.createElement("option", { value: "expansion" }, "Expansion"),
        React.createElement("option", { value: "downsell" }, "Downsell"),
      ),
      React.createElement("input", {
        value: form.description, onChange: e => setForm(f => ({ ...f, description: e.target.value })),
        placeholder: "Описание (например, подключаем рекомендации на карточке)",
        style: { padding: "6px 8px", background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 3, color: "var(--ink-8)", fontSize: 12 },
      }),
      React.createElement("input", {
        value: form.amount_after, onChange: e => setForm(f => ({ ...f, amount_after: e.target.value })),
        placeholder: "Ожидаемый MRR после (₽/мес)", type: "number",
        style: { padding: "6px 8px", background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 3, color: "var(--ink-8)", fontSize: 12 },
      }),
      React.createElement(Btn, { kind: "primary", size: "s", onClick: create, disabled: !form.description.trim() }, "Создать"),
    ),

    loading
      ? React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12 } }, "Загружаем…")
      : active.length === 0 && closed.length === 0
        ? React.createElement("div", { style: { color: "var(--ink-6)", fontSize: 12 } },
            "Нет активных апсейлов. Жми «+ Новый» если заметил возможность расширения.")
        : React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 8 } },
            active.map(e => React.createElement("div", {
              key: e.id,
              style: {
                padding: 10, background: "var(--ink-2)", border: "1px solid var(--line-soft)", borderRadius: 4,
              },
            },
              React.createElement("div", { style: { display: "flex", gap: 8, alignItems: "center", marginBottom: 4 } },
                React.createElement(Badge, { tone: statusColor[e.status] || "neutral", dot: true }, e.status),
                React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase" } }, e.event_type),
                e.amount_after && React.createElement("span", { className: "mono", style: { fontSize: 10.5, color: "var(--ink-7)", marginLeft: "auto" } },
                  "+" + Math.round(e.amount_after) + "₽"),
              ),
              React.createElement("div", { style: { fontSize: 12.5, color: "var(--ink-8)", marginBottom: 6, lineHeight: 1.4 } },
                e.description || "—"),
              React.createElement("div", { style: { display: "flex", gap: 4, flexWrap: "wrap" } },
                ["identified", "in_progress", "won", "lost", "postponed"].filter(s => s !== e.status).map(s =>
                  React.createElement("button", {
                    key: s,
                    onClick: () => updateStatus(e.id, s),
                    style: {
                      padding: "2px 8px", fontSize: 10.5,
                      background: "transparent", border: "1px solid var(--line)",
                      borderRadius: 3, color: "var(--ink-6)", cursor: "pointer",
                    },
                  }, "→ " + s))),
            )),
            closed.length > 0 && React.createElement("details", { style: { marginTop: 6 } },
              React.createElement("summary", { style: { fontSize: 11, color: "var(--ink-5)", cursor: "pointer" } },
                `Закрытые · ${closed.length}`),
              React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 4, marginTop: 6 } },
                closed.map(e => React.createElement("div", {
                  key: e.id, style: { fontSize: 11, color: "var(--ink-6)", padding: "4px 0" },
                }, `${e.status === "won" ? "✓" : "✕"} ${e.description || "—"}`))),
            ),
          ),
  );
}
window.ClientUpsellCard = ClientUpsellCard;


// ── BulkToolbar — массовые действия на /design/clients ──────────────────────
function BulkToolbar({ selectedIds, onClear, busy, setBusy }) {
  const ids = Array.from(selectedIds);
  const [users, setUsers] = React.useState([]);
  const [transferModal, setTransferModal] = React.useState(false);

  React.useEffect(() => {
    fetch("/api/admin/users", { credentials: "include" })
      .then(r => r.ok ? r.json() : [])
      .then(d => setUsers(Array.isArray(d) ? d : (d.users || [])))
      .catch(() => {});
  }, []);

  async function doAction(url, payload, successMsg) {
    if (busy) return;
    if (!window.confirm(`Действие затронет ${ids.length} клиентов. Продолжить?`)) return;
    setBusy(true);
    try {
      const r = await fetch(url, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_ids: ids, ...payload }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { window.appToast && window.appToast("Ошибка: " + (d.detail || r.status)); return; }
      window.appToast && window.appToast(successMsg.replace("{n}", String(d.touched || d.started || d.created || ids.length)));
      onClear();
      setTimeout(() => window.location.reload(), 800);
    } catch (e) {
      window.appToast && window.appToast("Ошибка: " + e.message);
    } finally { setBusy(false); }
  }

  const markCheckup = () => doAction("/api/clients/bulk/mark-checkup", {}, "✓ Чекап отмечен для {n}");
  const startOnboarding = () => doAction("/api/clients/bulk/start-onboarding", {}, "✓ Онбординг запущен для {n}");

  return React.createElement("div", {
    style: {
      position: "sticky", top: 0, zIndex: 50,
      display: "flex", alignItems: "center", gap: 10,
      padding: "10px 14px", marginBottom: 14,
      background: "var(--ink-1)", border: "1px solid var(--signal)",
      borderRadius: 6,
      boxShadow: "0 4px 14px rgba(0,0,0,.25)",
    },
  },
    React.createElement("span", { style: { fontSize: 13, fontWeight: 600, color: "var(--ink-9)" } },
      `Выбрано: ${ids.length}`),
    React.createElement("div", { style: { flex: 1 } }),
    React.createElement(Btn, { kind: "ghost", size: "s", onClick: markCheckup, disabled: busy }, "✓ Чекап проведён"),
    React.createElement(Btn, { kind: "ghost", size: "s", onClick: startOnboarding, disabled: busy }, "🚀 Онбординг"),
    React.createElement(Btn, { kind: "ghost", size: "s", onClick: () => setTransferModal(true), disabled: busy }, "🤝 Передать"),
    React.createElement(Btn, { kind: "ghost", size: "s", onClick: onClear, disabled: busy }, "Снять выделение"),

    transferModal && React.createElement(BulkTransferModal, {
      ids, users,
      onClose: () => setTransferModal(false),
      onDone: (n) => {
        window.appToast && window.appToast(`✓ Созданы запросы на передачу: ${n}`);
        setTransferModal(false); onClear();
        setTimeout(() => window.location.reload(), 800);
      },
    }),
  );
}
window.BulkToolbar = BulkToolbar;

function BulkTransferModal({ ids, users, onClose, onDone }) {
  const [toUserId, setToUserId] = React.useState("");
  const [note, setNote] = React.useState("");
  const [err, setErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const eligible = users.filter(u => ["manager", "grouphead", "admin"].includes(u.role));

  const submit = async () => {
    if (!toUserId) { setErr("Выбери менеджера"); return; }
    setBusy(true); setErr(null);
    try {
      const r = await fetch("/api/clients/bulk/transfer", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_ids: ids, to_user_id: Number(toUserId),
          manual_note: note,
        }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(d.detail || "HTTP " + r.status); return; }
      onDone(d.created || 0);
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return React.createElement("div", {
    onClick: (e) => { if (e.target === e.currentTarget) onClose(); },
    style: {
      position: "fixed", inset: 0, zIndex: 9999,
      background: "rgba(0,0,0,.55)", display: "flex",
      alignItems: "center", justifyContent: "center", padding: 24,
    },
  },
    React.createElement("div", {
      style: {
        background: "var(--ink-1)", border: "1px solid var(--line)",
        borderRadius: 8, padding: 20, width: 480, maxWidth: "100%",
      },
    },
      React.createElement("div", { style: { fontSize: 15, fontWeight: 600, marginBottom: 14 } },
        `🤝 Передать ${ids.length} клиентов`),
      err && React.createElement("div", { style: { fontSize: 12, color: "var(--critical)", marginBottom: 10 } }, err),
      React.createElement("div", { style: { fontSize: 11.5, color: "var(--ink-6)", marginBottom: 4 } }, "Новый менеджер:"),
      React.createElement("select", {
        value: toUserId, onChange: e => setToUserId(e.target.value),
        style: { width: "100%", padding: "8px 10px", marginBottom: 12, background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-8)" },
      },
        React.createElement("option", { value: "" }, "— выбери менеджера —"),
        eligible.map(u => React.createElement("option", { key: u.id, value: u.id }, `${u.email} (${u.role})`)),
      ),
      React.createElement("div", { style: { fontSize: 11.5, color: "var(--ink-6)", marginBottom: 4 } }, "Общая заметка (опционально):"),
      React.createElement("textarea", {
        value: note, onChange: e => setNote(e.target.value), rows: 3,
        placeholder: "Например: переход в рамках реорганизации",
        style: { width: "100%", resize: "vertical", padding: "8px 10px", marginBottom: 14, background: "var(--ink-2)", border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink-8)", fontFamily: "inherit", fontSize: 12.5 },
      }),
      React.createElement("div", { style: { fontSize: 11, color: "var(--ink-6)", marginBottom: 14, lineHeight: 1.5 } },
        `Для каждого клиента будет создан pending-запрос. Новый менеджер должен принять каждого. AI-сводка не генерируется (для массового переноса).`),
      React.createElement("div", { style: { display: "flex", justifyContent: "flex-end", gap: 8 } },
        React.createElement(Btn, { kind: "ghost", size: "m", onClick: onClose, disabled: busy }, "Отмена"),
        React.createElement(Btn, { kind: "primary", size: "m", onClick: submit, disabled: busy || !toUserId },
          busy ? "…" : "Создать запросы"),
      ),
    ),
  );
}
window.BulkTransferModal = BulkTransferModal;
