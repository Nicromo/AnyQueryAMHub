// shell.jsx — app shell: sidebar, topbar, layout

// Живые цифры приходят с сервера через window.__SIDEBAR_STATS
function _stats() {
  return (typeof window !== "undefined" && window.__SIDEBAR_STATS) || {};
}

// Внешние URL для пунктов с external: true. UI-константы — без бэка.
const EXTERNAL_URLS = {
  ktalk:      "https://tbank.ktalk.ru",
  merchrules: "https://merchrules.any-platform.ru/login-page",
};

function _buildNav() {
  const s = _stats();
  const num = (v) => (v == null ? undefined : String(v));
  return [
    { group: "", items: [
      { id: "command",  label: "Командный центр", icon: "command", badge: num(s.inbox) },
    ]},
    { group: "Ежедневное", items: [
      { id: "today",    label: "Сегодня",     icon: "sun" },
      { id: "clients",  label: "Все клиенты", icon: "users", count: num(s.clientsTotal) },
      { id: "top50",    label: "Top-50",      icon: "trophy" },
      { id: "tasks",    label: "Задачи",      icon: "check", count: num(s.tasksActive) },
      { id: "meetings", label: "Встречи",     icon: "cal",   count: num(s.meetingsUpcoming) },
      { id: "portfolio", label: "Портфель",   icon: "folder" },
    ]},
    { group: "Аналитика", items: [
      { id: "analytics", label: "Аналитика",  icon: "chart" },
      { id: "ai",        label: "AI-ассистент", icon: "bot", pill: "BETA" },
      { id: "kanban",    label: "Канбан",     icon: "kanban" },
      { id: "kpi",       label: "Мой KPI",    icon: "target" },
      { id: "qbr",       label: "QBR Календарь", icon: "map" },
    ]},
    { group: "Инструменты", items: [
      { id: "cabinet",   label: "Мой кабинет", icon: "folder" },
      { id: "templates", label: "Шаблоны",    icon: "doc" },
      { id: "auto",      label: "Автозадачи", icon: "spark" },
      { id: "roadmap",   label: "Роадмап",    icon: "map" },
      { id: "internal",  label: "Внутренние задачи", icon: "lock" },
    ]},
    { group: "Интеграции", items: [
      { id: "ktalk",      label: "KTalk",      icon: "video",  external: true },
      { id: "merchrules", label: "Merchrules", icon: "link",   external: true },
      { id: "extension",  label: "Расширение", icon: "puzzle", status: "ok" },
      { id: "help",       label: "Помощь",     icon: "help" },
    ]},
    { group: "Администрирование", items: [
      { id: "profile",     label: "Мой профиль",     icon: "users" },
      { id: "assignments", label: "Назначения",      icon: "folder" },
    ]},
  ];
}

function Sidebar({ active = "command", onNav }) {
  const NAV = _buildNav();
  const S = _stats();
  const _onItemClick = (item) => {
    if (item.external && EXTERNAL_URLS[item.id]) {
      window.open(EXTERNAL_URLS[item.id], "_blank", "noopener,noreferrer");
      return;
    }
    if (onNav) onNav(item.id);
  };
  return (
    <aside style={{
      width: 248, flexShrink: 0,
      background: "var(--ink-0)",
      borderRight: "1px solid var(--line)",
      height: "100vh", position: "sticky", top: 0,
      display: "flex", flexDirection: "column",
      overflow: "hidden",
    }}>
      {/* logo */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "16px 16px 14px",
        borderBottom: "1px solid var(--line-soft)",
      }}>
        <div style={{
          width: 28, height: 28,
          background: "var(--signal)", color: "var(--ink-0)",
          borderRadius: 5,
          display: "flex", alignItems: "center", justifyContent: "center",
          fontFamily: "var(--f-mono)", fontWeight: 700, fontSize: 14,
          letterSpacing: -0.5,
        }}>A</div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: "-0.01em" }}>AM&nbsp;Hub</div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", letterSpacing: "0.1em", textTransform: "uppercase" }}>
            ops console · v2
          </div>
        </div>
        <span style={{
          fontFamily: "var(--f-mono)", fontSize: 9, color: "var(--signal)",
          border: "1px solid color-mix(in oklch, var(--signal) 40%, transparent)",
          padding: "1px 5px", borderRadius: 2, letterSpacing: "0.1em",
        }}>BETA</span>
      </div>

      {/* search */}
      <div style={{ padding: "10px 12px 8px" }}>
        <button style={{
          display: "flex", alignItems: "center", gap: 10,
          width: "100%", height: 32,
          padding: "0 10px",
          background: "var(--ink-1)", border: "1px solid var(--line)",
          borderRadius: 4, color: "var(--ink-6)",
          fontSize: 12.5, textAlign: "left",
          fontFamily: "var(--f-display)",
          cursor: "pointer",
        }}>
          <I.search size={14}/>
          <span style={{ flex: 1 }}>Поиск клиента, задачи…</span>
          <Kbd>⌘</Kbd><Kbd>K</Kbd>
        </button>
      </div>

      {/* scroll region */}
      <nav style={{ flex: 1, overflowY: "auto", padding: "6px 8px 12px" }}>
        {NAV.map((section, i) => (
          <div key={i} style={{ marginTop: i === 0 ? 0 : 14 }}>
            {section.group && (
              <div className="mono" style={{
                fontSize: 10, color: "var(--ink-5)",
                textTransform: "uppercase", letterSpacing: "0.1em",
                padding: "6px 10px 6px",
              }}>{section.group}</div>
            )}
            {section.items.map(item => {
              const Ic = I[item.icon];
              const isActive = item.id === active;
              return (
                <a key={item.id} onClick={() => _onItemClick(item)}
                  style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "7px 10px",
                  borderRadius: 4, cursor: "pointer",
                  color: isActive ? "var(--ink-9)" : "var(--ink-7)",
                  background: isActive ? "var(--ink-2)" : "transparent",
                  fontSize: 13, fontWeight: isActive ? 500 : 400,
                  position: "relative",
                  textDecoration: "none",
                }}>
                  {isActive && <span style={{
                    position: "absolute", left: -8, top: 6, bottom: 6, width: 2,
                    background: "var(--signal)", borderRadius: 2,
                  }}/>}
                  <Ic size={15} stroke={isActive ? "var(--signal)" : "var(--ink-6)"} />
                  <span style={{ flex: 1, letterSpacing: "-0.003em" }}>{item.label}</span>
                  {item.count && (
                    <span className="mono" style={{
                      fontSize: 10.5, color: "var(--ink-6)",
                      background: "var(--ink-3)", padding: "1px 6px", borderRadius: 3,
                    }}>{item.count}</span>
                  )}
                  {item.pill && <Badge tone="signal" style={{ padding: "0px 5px", fontSize: 9 }}>{item.pill}</Badge>}
                  {item.badge && (
                    <span style={{
                      background: "var(--critical)", color: "var(--ink-9)",
                      fontFamily: "var(--f-mono)", fontSize: 9.5, fontWeight: 600,
                      padding: "1px 5px", borderRadius: 2, letterSpacing: 0,
                    }}>{item.badge}</span>
                  )}
                  {item.external && <I.arrow_r size={11} stroke="var(--ink-5)"/>}
                  {item.status === "ok" && <span style={{ width: 5, height: 5, borderRadius: 999, background: "var(--ok)", boxShadow: "0 0 8px var(--ok)" }}/>}
                </a>
              );
            })}
          </div>
        ))}

        {/* sidebar stats block */}
        <div style={{
          margin: "18px 6px 0",
          padding: "12px",
          background: "var(--ink-1)",
          border: "1px solid var(--line)",
          borderRadius: 5,
          display: "flex", flexDirection: "column", gap: 9,
        }}>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-5)", textTransform: "uppercase", letterSpacing: "0.1em" }}>
            системные сигналы
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 5, height: 5, borderRadius: 999, background: "var(--critical)", boxShadow: "0 0 8px var(--critical)" }}/>
            <span style={{ fontSize: 12, color: "var(--ink-8)" }}>{S.overdue || 0} просрочено</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 5, height: 5, borderRadius: 999, background: "var(--warn)" }}/>
            <span style={{ fontSize: 12, color: "var(--ink-8)" }}>{S.dueCheckup || 0} скоро чекап</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 5, height: 5, borderRadius: 999, background: "var(--signal)" }}/>
            <span style={{ fontSize: 12, color: "var(--ink-8)" }}>{S.tasksActive || 0} активных задач</span>
          </div>
        </div>
      </nav>

      {/* bottom */}
      <div style={{
        borderTop: "1px solid var(--line-soft)",
        padding: "10px 12px",
        display: "flex", alignItems: "center", gap: 10,
      }}>
        {(() => {
          const U = (typeof window !== "undefined" && window.__CURRENT_USER) || {};
          const name = U.name || U.email || "—";
          const role = U.role || "user";
          return (
            <>
              <Avatar name={name} size={28} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12.5, fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {name}
                </div>
                <div className="mono" style={{ fontSize: 10, color: "var(--ink-5)" }}>{role}</div>
              </div>
            </>
          );
        })()}
        <ThemeToggle/>
        <button
          title="Выйти"
          onClick={() => { window.location.href = "/logout"; }}
          style={{
            background: "transparent", border: 0,
            color: "var(--ink-6)", cursor: "pointer", padding: 6,
            display: "inline-flex", alignItems: "center", justifyContent: "center",
          }}
        >
          <I.signout size={15}/>
        </button>
      </div>
    </aside>
  );
}

// ── TopBar ──────────────────────────────────────────────────
function TopBar({ title, subtitle, breadcrumbs = [], actions, meta }) {
  return (
    <header style={{
      display: "flex", alignItems: "center",
      padding: "18px 28px 16px",
      borderBottom: "1px solid var(--line-soft)",
      background: "var(--ink-1)",
      position: "sticky", top: 0, zIndex: 10,
      gap: 18,
      flexWrap: "wrap",
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        {breadcrumbs.length > 0 && (
          <div className="mono" style={{
            fontSize: 10.5, color: "var(--ink-5)",
            textTransform: "uppercase", letterSpacing: "0.1em",
            display: "flex", gap: 6, alignItems: "center", marginBottom: 6,
          }}>
            {breadcrumbs.map((b, i) => (
              <React.Fragment key={i}>
                {i > 0 && <span>/</span>}
                <span style={{ color: i === breadcrumbs.length - 1 ? "var(--ink-7)" : "var(--ink-5)" }}>{b}</span>
              </React.Fragment>
            ))}
          </div>
        )}
        <h1 style={{
          margin: 0, fontSize: 22, fontWeight: 500,
          letterSpacing: "-0.02em", color: "var(--ink-9)",
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        }}>{title}</h1>
        {subtitle && (
          <div style={{ fontSize: 13, color: "var(--ink-6)", marginTop: 3 }}>{subtitle}</div>
        )}
      </div>

      {meta && <div style={{ display: "flex", gap: 18, alignItems: "center" }}>{meta}</div>}

      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        {actions}
        <ThemeToggle/>
        <button
          title="Уведомления"
          onClick={() => { window.location.href = "/inbox"; }}
          style={{
            width: 34, height: 34, background: "transparent",
            border: "1px solid var(--line)", borderRadius: 4,
            color: "var(--ink-7)", cursor: "pointer",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            position: "relative",
          }}
        >
          <I.bell size={15}/>
          <span style={{
            position: "absolute", top: 4, right: 5,
            width: 6, height: 6, borderRadius: 999, background: "var(--critical)",
          }}/>
        </button>
        <button
          title="Выйти"
          onClick={() => { window.location.href = "/logout"; }}
          style={{
            width: 34, height: 34, background: "transparent",
            border: "1px solid var(--line)", borderRadius: 4,
            color: "var(--ink-7)", cursor: "pointer",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
          }}
        >
          <I.signout size={15}/>
        </button>
      </div>
    </header>
  );
}

// ── Theme toggle — persists in localStorage, flips html[data-theme] ──
function ThemeToggle() {
  const getInitial = () => {
    if (typeof document === "undefined") return "dark";
    return document.documentElement.getAttribute("data-theme") || "dark";
  };
  const [theme, setTheme] = React.useState(getInitial);
  React.useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("amhub-theme", theme); } catch (e) {}
  }, [theme]);
  const flip = () => setTheme((t) => (t === "dark" ? "light" : "dark"));
  return (
    <button onClick={flip}
      title={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
      style={{
        width: 34, height: 34, background: "transparent",
        border: "1px solid var(--line)", borderRadius: 4,
        color: "var(--ink-7)", cursor: "pointer",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
      }}>
      {theme === "dark" ? <I.sun size={15}/> : <I.moon size={15}/>}
    </button>
  );
}

// ── Layout ──────────────────────────────────────────────────
function Shell({ active, onNav, children }) {
  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--ink-1)" }}>
      <Sidebar active={active} onNav={onNav} />
      <main style={{ flex: 1, minWidth: 0 }}>{children}</main>
      <SearchOverlay />
      <TaskCreateFAB />
    </div>
  );
}


// ══════════════════════════════════════════════════════════════
// SearchOverlay — ⌘K / Ctrl+K глобальный поиск.
// Эндпоинт /api/search уже существует на бэке (misc_small.py).
// ══════════════════════════════════════════════════════════════
function SearchOverlay() {
  const [open, setOpen] = React.useState(false);
  const [q, setQ] = React.useState("");
  const [results, setResults] = React.useState({ clients: [], tasks: [], meetings: [] });
  const [loading, setLoading] = React.useState(false);
  const inputRef = React.useRef(null);

  // Глобальный слушатель ⌘K / Ctrl+K / Esc
  React.useEffect(() => {
    const onKey = (e) => {
      const isCmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      if (isCmdK) { e.preventDefault(); setOpen(true); }
      else if (e.key === "Escape") { setOpen(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  React.useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus();
  }, [open]);

  // Debounced fetch /api/search
  React.useEffect(() => {
    if (!q.trim()) { setResults({ clients: [], tasks: [], meetings: [] }); return; }
    setLoading(true);
    const t = setTimeout(() => {
      fetch("/api/search?q=" + encodeURIComponent(q) + "&limit=8", { credentials: "include" })
        .then(r => r.ok ? r.json() : { clients: [], tasks: [], meetings: [] })
        .then(d => {
          setResults({
            clients:  d.clients  || [],
            tasks:    d.tasks    || [],
            meetings: d.meetings || [],
          });
          setLoading(false);
        })
        .catch(() => setLoading(false));
    }, 180);
    return () => clearTimeout(t);
  }, [q]);

  if (!open) return null;

  const hasResults = results.clients.length + results.tasks.length + results.meetings.length > 0;

  return (
    <div onClick={() => setOpen(false)} style={{
      position: "fixed", inset: 0, zIndex: 9000,
      background: "color-mix(in oklch, var(--ink-0) 75%, transparent)",
      backdropFilter: "blur(6px)",
      display: "flex", justifyContent: "center",
      paddingTop: "12vh",
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: "min(620px, 92vw)",
        background: "var(--ink-1)",
        border: "1px solid var(--line)",
        borderRadius: 10,
        boxShadow: "0 24px 60px rgba(0,0,0,0.5)",
        overflow: "hidden",
      }}>
        <div style={{
          display: "flex", alignItems: "center", gap: 12,
          padding: "14px 18px",
          borderBottom: "1px solid var(--line-soft)",
        }}>
          <I.search size={16} stroke="var(--ink-6)"/>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Поиск клиента, задачи, встречи…"
            style={{
              flex: 1, background: "transparent", border: 0, outline: "none",
              color: "var(--ink-9)", fontSize: 15, fontFamily: "var(--f-display)",
              letterSpacing: "-0.005em",
            }}
          />
          <span className="mono" style={{ fontSize: 10, color: "var(--ink-5)" }}>
            {loading ? "…" : "ESC"}
          </span>
        </div>

        <div style={{ maxHeight: "50vh", overflowY: "auto" }}>
          {!q.trim() && (
            <div style={{ padding: "28px 20px", color: "var(--ink-6)", fontSize: 13, textAlign: "center" }}>
              Начните набирать — поиск по клиентам, задачам и встречам.
            </div>
          )}
          {q.trim() && !hasResults && !loading && (
            <div style={{ padding: "28px 20px", color: "var(--ink-6)", fontSize: 13, textAlign: "center" }}>
              Ничего не найдено
            </div>
          )}

          {results.clients.length > 0 && (
            <_SearchSection title="Клиенты" items={results.clients.map(c => ({
              key: "c" + c.id,
              label: c.name,
              hint: c.segment || "",
              href: "/design/client/" + c.id,
              icon: "users",
            }))}/>
          )}
          {results.tasks.length > 0 && (
            <_SearchSection title="Задачи" items={results.tasks.map(t => ({
              key: "t" + t.id,
              label: t.title,
              hint: t.client_name || "",
              href: "/design/tasks",
              icon: "check",
            }))}/>
          )}
          {results.meetings.length > 0 && (
            <_SearchSection title="Встречи" items={results.meetings.map(m => ({
              key: "m" + m.id,
              label: m.title || m.type || "встреча",
              hint: m.client_name || "",
              href: "/design/meetings",
              icon: "cal",
            }))}/>
          )}
        </div>
      </div>
    </div>
  );
}

function _SearchSection({ title, items }) {
  return (
    <div style={{ padding: "6px 0" }}>
      <div className="mono" style={{
        padding: "8px 18px 6px",
        fontSize: 10, color: "var(--ink-5)",
        textTransform: "uppercase", letterSpacing: "0.1em",
      }}>{title}</div>
      {items.map(it => {
        const Ic = I[it.icon] || I.users;
        return (
          <a key={it.key} href={it.href} style={{
            display: "flex", alignItems: "center", gap: 12,
            padding: "9px 18px", textDecoration: "none",
            color: "var(--ink-8)", fontSize: 13,
            cursor: "pointer",
          }} onMouseEnter={(e) => e.currentTarget.style.background = "var(--ink-2)"}
             onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
          >
            <Ic size={14} stroke="var(--ink-6)"/>
            <span style={{ flex: 1, color: "var(--ink-9)" }}>{it.label}</span>
            {it.hint && <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-5)" }}>{it.hint}</span>}
          </a>
        );
      })}
    </div>
  );
}


// ══════════════════════════════════════════════════════════════
// TaskCreateFAB — плавающая кнопка "+" + модалка создания задачи.
// POST /api/tasks уже существует на бэке (routers/tasks.py).
// ══════════════════════════════════════════════════════════════
function TaskCreateFAB() {
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title="Новая задача"
        style={{
          position: "fixed", right: 24, bottom: 24, zIndex: 8000,
          width: 52, height: 52, borderRadius: 999,
          background: "var(--signal)", color: "var(--ink-0)",
          border: "none", cursor: "pointer",
          display: "flex", alignItems: "center", justifyContent: "center",
          boxShadow: "0 8px 24px var(--signal-glow), 0 2px 8px rgba(0,0,0,0.3)",
          fontSize: 28, fontWeight: 300, lineHeight: 1,
          fontFamily: "var(--f-display)",
        }}
      >+</button>
      {open && <TaskCreateModal onClose={() => setOpen(false)} />}
    </>
  );
}

function TaskCreateModal({ onClose }) {
  const [title, setTitle] = React.useState("");
  const [clientId, setClientId] = React.useState(
    (window.__CURRENT_CLIENT && window.__CURRENT_CLIENT.id) ||
    (window.CLIENTS && window.CLIENTS[0] && window.CLIENTS[0].id) || ""
  );
  const [priority, setPriority] = React.useState("medium");
  const [dueDate, setDueDate] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");

  const canSave = title.trim() && clientId && !saving;

  const submit = async () => {
    if (!canSave) return;
    setSaving(true); setError("");
    try {
      const body = {
        client_id: Number(clientId),
        title:     title.trim(),
        priority:  priority,
        status:    "plan",
      };
      if (dueDate) body.due_date = dueDate + "T12:00:00";
      const r = await fetch("/api/tasks", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      if (!data.ok) throw new Error("Не удалось создать задачу");
      window.location.reload();  // серверный редер подтянет новую задачу
    } catch (e) {
      setError(String(e.message || e));
      setSaving(false);
    }
  };

  const clients = window.CLIENTS || [];

  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, zIndex: 9100,
      background: "color-mix(in oklch, var(--ink-0) 75%, transparent)",
      backdropFilter: "blur(6px)",
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: "min(480px, 92vw)",
        background: "var(--ink-1)",
        border: "1px solid var(--line)",
        borderRadius: 10,
        boxShadow: "0 24px 60px rgba(0,0,0,0.5)",
        padding: 24,
      }}>
        <div style={{ display: "flex", alignItems: "center", marginBottom: 20 }}>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 500, letterSpacing: "-0.01em", color: "var(--ink-9)" }}>
            Новая задача
          </h2>
          <button onClick={onClose} style={{
            marginLeft: "auto", background: "transparent", border: 0,
            color: "var(--ink-6)", cursor: "pointer", fontSize: 20, padding: 4,
          }}>×</button>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <_Field label="Название">
            <input value={title} onChange={(e) => setTitle(e.target.value)}
              autoFocus placeholder="Что нужно сделать…"
              style={_inputStyle()}/>
          </_Field>

          <_Field label="Клиент">
            <select value={clientId} onChange={(e) => setClientId(e.target.value)} style={_inputStyle()}>
              {clients.length === 0 && <option value="">— нет доступных клиентов —</option>}
              {clients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </_Field>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <_Field label="Приоритет">
              <select value={priority} onChange={(e) => setPriority(e.target.value)} style={_inputStyle()}>
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
                <option value="critical">critical</option>
              </select>
            </_Field>
            <_Field label="Срок">
              <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)}
                style={_inputStyle()}/>
            </_Field>
          </div>

          {error && <div style={{
            fontSize: 12, color: "var(--critical)",
            padding: "8px 12px",
            background: "color-mix(in oklch, var(--critical) 10%, transparent)",
            border: "1px solid color-mix(in oklch, var(--critical) 30%, transparent)",
            borderRadius: 4,
          }}>{error}</div>}

          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 6 }}>
            <Btn kind="ghost" size="m" onClick={onClose}>Отмена</Btn>
            <Btn kind="primary" size="m" onClick={submit}
              icon={<I.check size={14}/>}>
              {saving ? "Создаём…" : "Создать"}
            </Btn>
          </div>
        </div>
      </div>
    </div>
  );
}

function _Field({ label, children }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span className="mono" style={{
        fontSize: 10, color: "var(--ink-5)",
        textTransform: "uppercase", letterSpacing: "0.1em",
      }}>{label}</span>
      {children}
    </label>
  );
}

function _inputStyle() {
  return {
    height: 36, padding: "0 12px",
    background: "var(--ink-2)",
    border: "1px solid var(--line)",
    borderRadius: 5,
    color: "var(--ink-9)",
    fontSize: 13,
    fontFamily: "var(--f-display)",
    outline: "none",
  };
}


Object.assign(window, { Shell, Sidebar, TopBar });
