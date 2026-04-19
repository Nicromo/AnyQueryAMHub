// page_clients.jsx — Clients list & Client detail

function PageClients() {
  const P = (typeof window !== "undefined" && window.__PAGINATION) || { page: 1, total: 0, total_pages: 1, has_prev: false, has_next: false };
  const CL = (typeof window !== "undefined" && window.CLIENTS) || [];
  const [segFilter, setSegFilter] = React.useState("all");
  const [newClientModal, setNewClientModal] = React.useState(false);

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
        alert("Airtable: " + d.error);
      } else {
        alert(`Готово. ${d.message || JSON.stringify(d).slice(0, 200)}`);
        location.reload();
      }
    } catch (e) {
      alert("Ошибка: " + e.message);
    } finally {
      setSyncBusy(false);
    }
  }

  return (
    <div>
      <TopBar
        breadcrumbs={["am hub", "портфель"]}
        title="Все клиенты"
        subtitle={`${P.total} клиентов · стр. ${P.page} из ${P.total_pages}`}
        actions={
          <>
            <Btn kind="ghost" size="m" onClick={pullFromAirtable} disabled={syncBusy}>
              {syncBusy ? "Тянем..." : "⟲ Из Airtable"}
            </Btn>
            <Btn kind="ghost" size="m" onClick={async () => {
              if (!window.confirm("Объединить дубли клиентов по нормализованному имени? 'Yves Rocher' и 'yves-rocher' станут одним.")) return;
              const r = await fetch("/api/clients/auto-dedupe", {method:"POST", credentials:"include"});
              const d = await r.json().catch(()=>({}));
              alert(d.ok ? `Объединено: ${d.merged}` : (d.error || "Ошибка"));
              if (d.ok) location.reload();
            }}>⎘ Дубли</Btn>
            <Btn kind="ghost" size="m" icon={<I.download size={14}/>}
              onClick={() => window.open("/api/clients/export?format=csv", "_blank")}>Экспорт</Btn>
            <Btn kind="primary" size="m" icon={<I.plus size={14}/>}
              onClick={() => setNewClientModal(true)}>Новый клиент</Btn>
          </>
        }
      />
      <div style={{ padding: "22px 28px 40px" }}>
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
            gridTemplateColumns: "6px 2.2fr 1fr 1.4fr 1.2fr 30px",
            gap: 16,
            padding: "10px 18px",
            background: "var(--ink-1)",
            borderBottom: "1px solid var(--line)",
            fontFamily: "var(--f-mono)", fontSize: 10,
            textTransform: "uppercase", letterSpacing: "0.08em",
            color: "var(--ink-5)", alignItems: "center",
          }}>
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
                gridTemplateColumns: "6px 2.2fr 1fr 1.4fr 1.2fr 30px",
                gap: 16,
                padding: "14px 18px",
                borderBottom: i === visibleClients.length - 1 ? "none" : "1px solid var(--line-soft)",
                alignItems: "center",
                cursor: "pointer",
              }}>
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
      {newClientModal && (
        <FormModal
          title="Новый клиент"
          fields={[{ key: "name", label: "Название клиента", type: "text", placeholder: "ООО Ромашка", required: true }]}
          submitLabel="Создать"
          onClose={() => setNewClientModal(false)}
          onSubmit={(vals) =>
            fetch("/api/clients", {
              method: "POST", credentials: "include",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ name: vals.name }),
            })
              .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
              .then(() => { setNewClientModal(false); location.reload(); })
          }
        />
      )}
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

  return (
    <div>
      <TopBar
        breadcrumbs={["am hub", "клиенты", c.name]}
        title={c.name}
        subtitle={[segment !== "—" && `Сегмент ${segment}`, domain !== "—" && domain, managerEmail !== "—" && "AM: " + managerEmail].filter(Boolean).join(" · ")}
        actions={
          <>
            <Btn kind="ghost" size="m" icon={<I.chat size={14}/>} onClick={async () => {
              const txt = window.prompt("Новая заметка по клиенту:");
              if (!txt) return;
              const r = await fetch(`/api/clients/${c.id}/notes`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ content: txt }),
              });
              if (r.ok) window.location.reload();
              else alert("Не удалось сохранить заметку");
            }}>Заметка</Btn>
            <Btn kind="ghost" size="m" icon={<I.cal size={14}/>} onClick={() => {
              window.location.href = `/design/meetings?client_id=${c.id}`;
            }}>Запланировать</Btn>
            <Btn kind="primary" size="m" icon={<I.lightning size={14}/>} onClick={() => {
              window.location.href = `/design/followup?client_id=${c.id}`;
            }}>Follow-up</Btn>
          </>
        }
      />
      <div style={{ padding: "22px 28px 40px", display: "flex", flexDirection: "column", gap: 18 }}>

        {/* top strip — реальные данные клиента */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12 }}>
          <KPI label="Сегмент" value={segment} tone={segment !== "—" ? "signal" : "neutral"}/>
          <KPI label="GMV · 30д" value={gmv} sub={c.revenue_trend || undefined}/>
          <KPI label="Health score" value={health != null ? String(health) : "—"} tone={health == null ? "neutral" : health < 40 ? "err" : health < 70 ? "warn" : "ok"} sub={health == null ? "данные не синкнуты" : undefined}/>
          <KPI label="Открытых задач" value={openTasks != null ? String(openTasks) : "—"} sub={openTasks == null ? "синк не делался" : undefined}/>
          <KPI label="Последний контакт" value={lastContactStr} sub={c.domain ? "домен: " + c.domain : undefined}/>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 18 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0 }}>

            {/* AI summary */}
            <Card title="AI-бриф перед встречей" action={<Badge tone="signal">auto · 09:12</Badge>}>
              <div style={{ fontSize: 13.5, color: "var(--ink-8)", lineHeight: 1.6 }}>
                Клиент растёт <span style={{ color: "var(--signal)" }}>+12% MoM</span>, однако за последние 7 дней
                воронка оплаты просела на 6%. Drilldown показывает: проблема на шаге &laquo;адрес&raquo; —
                видимо связано с обновлением Merchrules от 11 апр. Рекомендую: поднять лимит ежедневного
                вывода до 1.2М и обсудить запуск программы лояльности.
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
                <Btn size="s" kind="dim">Принять всё</Btn>
                <Btn size="s" kind="ghost">Показать источники</Btn>
                <Btn size="s" kind="ghost">Обновить</Btn>
              </div>
            </Card>

            {/* activity timeline */}
            <Card title="Хронология">
              <div style={{ position: "relative", paddingLeft: 20 }}>
                <div style={{ position: "absolute", left: 5, top: 4, bottom: 4, width: 1, background: "var(--line)" }}/>
                {[
                  { when: "сегодня", title: "Запланирован чекап 14:00", sub: "KTalk · встреча с Ольгой Л. (CMO)", tone: "signal" },
                  { when: "2 дня назад", title: "Отправлен follow-up по новым правилам", sub: "email · 1 ответ получен", tone: "ok" },
                  { when: "5 дней назад", title: "GMV перешагнул рубеж ₽4м", sub: "+12% за 30 дней", tone: "ok" },
                  { when: "11 дней назад", title: "Поднят лимит вывода до 800к", sub: "согласовано с финансами", tone: "neutral" },
                  { when: "21 день назад", title: "Чекап проведён · настроение positive", sub: "заметка: обсуждали интеграцию с Diginetica", tone: "neutral" },
                ].map((e, i) => (
                  <div key={i} style={{ position: "relative", paddingBottom: 18 }}>
                    <div style={{
                      position: "absolute", left: -20, top: 4, width: 11, height: 11, borderRadius: 999,
                      background: "var(--ink-1)",
                      border: `2px solid ${e.tone === "signal" ? "var(--signal)" : e.tone === "ok" ? "var(--ok)" : "var(--ink-4)"}`,
                    }}/>
                    <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{e.when}</div>
                    <div style={{ fontSize: 13.5, color: "var(--ink-9)", fontWeight: 500, marginTop: 2 }}>{e.title}</div>
                    <div style={{ fontSize: 12, color: "var(--ink-6)", marginTop: 2 }}>{e.sub}</div>
                  </div>
                ))}
              </div>
            </Card>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0 }}>
            <Card title="Контакты">
              {[
                { name: "Ольга Ларина", role: "CMO · главное лицо", ch: "KTalk · live" },
                { name: "Денис Ткач", role: "Финансы", ch: "email" },
                { name: "Алиса Р.", role: "Operations", ch: "Telegram" },
              ].map((p, i) => (
                <div key={i} style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "10px 0",
                  borderBottom: i === 2 ? "none" : "1px solid var(--line-soft)",
                }}>
                  <Avatar name={p.name} size={32}/>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12.5, fontWeight: 500, color: "var(--ink-8)" }}>{p.name}</div>
                    <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>{p.role}</div>
                  </div>
                  <span className="mono" style={{ fontSize: 10, color: "var(--ink-6)" }}>{p.ch}</span>
                </div>
              ))}
            </Card>

            <Card title="Документы и договоры">
              {[
                { name: "Договор №2241", status: "подписан", date: "15 мар 2022" },
                { name: "Допсоглашение Q2'26", status: "на подписи", date: "12 апр" },
                { name: "API ключ Merchrules", status: "активен", date: "ротация 01.06" },
              ].map((d, i) => (
                <div key={i} style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "10px 0",
                  borderBottom: i === 2 ? "none" : "1px solid var(--line-soft)",
                }}>
                  <I.doc size={16} stroke="var(--ink-6)"/>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12.5, color: "var(--ink-8)" }}>{d.name}</div>
                    <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>{d.date}</div>
                  </div>
                  <Badge tone={d.status === "на подписи" ? "warn" : "ok"} dot>{d.status}</Badge>
                </div>
              ))}
            </Card>

            <Card title="Теги">
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {["tier-1", "строймат", "мск", "diginetica-интерес", "qbr-квартальный", "telegram-ok"].map(t => (
                  <Badge key={t} tone="ghost" mono>{t}</Badge>
                ))}
              </div>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}

window.PageClients = PageClients;
window.PageClient = PageClient;

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
