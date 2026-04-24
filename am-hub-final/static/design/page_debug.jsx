/* Debug Panel — integration health, sync logs, API tester. Admin only. */

function PageDebug({ initialData }) {
  const [overview, setOverview] = React.useState(null);
  const [logs, setLogs] = React.useState([]);
  const [logsTotal, setLogsTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [logsLoading, setLogsLoading] = React.useState(false);
  const [tab, setTab] = React.useState("integrations"); // integrations | logs | errors | audit
  const [filterIntegration, setFilterIntegration] = React.useState("");
  const [filterStatus, setFilterStatus] = React.useState("");
  const [pingResults, setPingResults] = React.useState({});
  const [pingLoading, setPingLoading] = React.useState({});
  const [expandedLog, setExpandedLog] = React.useState(null);
  const [auditLogs, setAuditLogs] = React.useState([]);

  React.useEffect(() => {
    loadOverview();
  }, []);

  React.useEffect(() => {
    if (tab === "logs" || tab === "errors") loadLogs();
    if (tab === "audit") loadAudit();
  }, [tab, filterIntegration, filterStatus]);

  async function loadOverview() {
    setLoading(true);
    try {
      const r = await fetch("/api/debug/overview");
      if (r.ok) setOverview(await r.json());
    } finally {
      setLoading(false);
    }
  }

  async function loadLogs() {
    setLogsLoading(true);
    try {
      const params = new URLSearchParams({ limit: 100, offset: 0 });
      if (filterIntegration) params.set("integration", filterIntegration);
      const statusFilter = tab === "errors" ? "error" : filterStatus;
      if (statusFilter) params.set("status", statusFilter);
      const r = await fetch(`/api/debug/logs?${params}`);
      if (r.ok) {
        const d = await r.json();
        setLogs(d.logs || []);
        setLogsTotal(d.total || 0);
      }
    } finally {
      setLogsLoading(false);
    }
  }

  async function loadAudit() {
    const r = await fetch("/api/debug/audit?limit=50");
    if (r.ok) setAuditLogs((await r.json()).logs || []);
  }

  async function ping(integration, testUrl) {
    if (!testUrl) return;
    setPingLoading(p => ({ ...p, [integration]: true }));
    setPingResults(p => ({ ...p, [integration]: null }));
    const start = Date.now();
    try {
      const r = await fetch(testUrl);
      const data = await r.json();
      setPingResults(p => ({
        ...p,
        [integration]: {
          ok: r.ok && (data.ok !== false),
          status: r.status,
          ms: Date.now() - start,
          data,
        },
      }));
    } catch (e) {
      setPingResults(p => ({ ...p, [integration]: { ok: false, error: e.message, ms: Date.now() - start } }));
    } finally {
      setPingLoading(p => ({ ...p, [integration]: false }));
    }
  }

  function statusBadge(status) {
    const colors = {
      ok: "#22c55e", success: "#22c55e",
      error: "#ef4444",
      warning: "#f59e0b", warn: "#f59e0b",
      running: "#3b82f6",
      skipped: "#94a3b8",
    };
    const c = colors[status] || "#94a3b8";
    return (
      <span style={{
        display: "inline-block", padding: "2px 8px", borderRadius: 99,
        fontSize: 11, fontWeight: 600, color: "#fff",
        background: c, textTransform: "uppercase", letterSpacing: "0.05em",
      }}>{status}</span>
    );
  }

  function directionIcon(dir) {
    if (dir === "bidirectional") return "⇄";
    if (dir === "inbound") return "↓";
    return "↑";
  }

  function fmtDur(ms) {
    if (ms == null) return "—";
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  function fmtDt(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString("ru-RU", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  function timeAgo(iso) {
    if (!iso) return "никогда";
    const sec = Math.floor((Date.now() - new Date(iso)) / 1000);
    if (sec < 60) return `${sec}с назад`;
    if (sec < 3600) return `${Math.floor(sec / 60)}м назад`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}ч назад`;
    return `${Math.floor(sec / 86400)}д назад`;
  }

  const cardStyle = {
    background: "#1e293b", borderRadius: 12, padding: "16px 20px",
    border: "1px solid #334155", marginBottom: 12,
  };
  const tabStyle = (active) => ({
    padding: "8px 18px", borderRadius: 8, cursor: "pointer", fontWeight: 600,
    fontSize: 13, border: "none", marginRight: 6,
    background: active ? "#3b82f6" : "#1e293b",
    color: active ? "#fff" : "#94a3b8",
  });

  if (loading) return (
    <div style={{ color: "#94a3b8", textAlign: "center", marginTop: 60 }}>Загрузка...</div>
  );

  const stats = overview?.stats_24h;

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "0 0 60px" }}>
      {/* Stats bar */}
      {stats && (
        <div style={{ display: "flex", gap: 12, marginBottom: 24, flexWrap: "wrap" }}>
          {[
            { label: "Синков за 24ч", value: stats.total_syncs },
            { label: "Ошибок за 24ч", value: stats.errors, color: stats.errors > 0 ? "#ef4444" : "#22c55e" },
            { label: "Записей обработано", value: stats.records_processed.toLocaleString("ru-RU") },
          ].map(s => (
            <div key={s.label} style={{ ...cardStyle, flex: "1 1 160px", minWidth: 140, marginBottom: 0, padding: "12px 18px" }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: s.color || "#f8fafc" }}>{s.value}</div>
              <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 2 }}>{s.label}</div>
            </div>
          ))}
          <div style={{ ...cardStyle, flex: "1 1 160px", minWidth: 140, marginBottom: 0, padding: "12px 18px", display: "flex", alignItems: "center", gap: 10 }}>
            <button onClick={loadOverview} style={{
              background: "#334155", border: "none", color: "#f8fafc", padding: "8px 16px",
              borderRadius: 8, cursor: "pointer", fontWeight: 600, fontSize: 13,
            }}>↻ Обновить</button>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div style={{ marginBottom: 20 }}>
        {[
          ["integrations", "Интеграции"],
          ["logs", "Лог синков"],
          ["errors", `Ошибки${overview?.recent_errors?.length ? ` (${overview.recent_errors.length})` : ""}`],
          ["audit", "Аудит"],
        ].map(([key, label]) => (
          <button key={key} style={tabStyle(tab === key)} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>

      {/* Tab: Integrations */}
      {tab === "integrations" && overview && (
        <div>
          {overview.integrations.map(intg => {
            const ping_ = pingResults[intg.key];
            const pinging = pingLoading[intg.key];
            return (
              <div key={intg.key} style={{
                ...cardStyle,
                borderLeft: `4px solid ${intg.configured ? "#22c55e" : "#ef4444"}`,
                display: "flex", gap: 16, flexWrap: "wrap",
              }}>
                {/* Name + status */}
                <div style={{ flex: "1 1 200px", minWidth: 180 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: 15, fontWeight: 700, color: "#f8fafc" }}>{intg.label}</span>
                    <span style={{ fontSize: 11, color: "#64748b", border: "1px solid #334155", borderRadius: 4, padding: "1px 6px" }}>
                      {directionIcon(intg.direction)} {intg.direction}
                    </span>
                  </div>
                  <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 4 }}>{intg.description}</div>
                  {!intg.configured && intg.missing_env.length > 0 && (
                    <div style={{ fontSize: 11, color: "#ef4444", marginTop: 6 }}>
                      Не настроено: {intg.missing_env.join(", ")}
                    </div>
                  )}
                  {intg.errors_7d > 0 && (
                    <div style={{ fontSize: 11, color: "#f59e0b", marginTop: 4 }}>
                      ⚠ {intg.errors_7d} ошибок за 7 дней
                    </div>
                  )}
                </div>

                {/* Last sync */}
                <div style={{ flex: "1 1 220px" }}>
                  {intg.last_sync ? (
                    <>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                        {statusBadge(intg.last_sync.status)}
                        <span style={{ fontSize: 12, color: "#64748b" }}>{timeAgo(intg.last_sync.started_at)}</span>
                      </div>
                      {intg.last_sync.message && (
                        <div style={{ fontSize: 12, color: "#94a3b8", lineHeight: 1.4 }}>
                          {intg.last_sync.message.slice(0, 120)}
                        </div>
                      )}
                      <div style={{ fontSize: 11, color: "#475569", marginTop: 4 }}>
                        {intg.last_sync.records} записей · {intg.last_sync.errors} ошибок
                      </div>
                    </>
                  ) : (
                    <span style={{ fontSize: 12, color: "#475569" }}>Синков нет</span>
                  )}
                </div>

                {/* Ping button */}
                <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", gap: 6 }}>
                  {intg.test_url ? (
                    <>
                      <button
                        onClick={() => ping(intg.key, intg.test_url)}
                        disabled={pinging}
                        style={{
                          background: pinging ? "#334155" : "#1d4ed8", color: "#fff",
                          border: "none", borderRadius: 8, padding: "7px 14px",
                          cursor: pinging ? "default" : "pointer", fontWeight: 600, fontSize: 12,
                        }}
                      >{pinging ? "Проверяем..." : "Проверить"}</button>
                      {ping_ && (
                        <div style={{
                          fontSize: 11, padding: "4px 8px", borderRadius: 6, textAlign: "center",
                          background: ping_.ok ? "#14532d" : "#450a0a",
                          color: ping_.ok ? "#86efac" : "#fca5a5",
                        }}>
                          {ping_.ok ? "✓ OK" : "✗ Ошибка"} · {ping_.ms}ms
                          {ping_.error && <div style={{ marginTop: 2 }}>{ping_.error.slice(0, 80)}</div>}
                        </div>
                      )}
                    </>
                  ) : (
                    <span style={{ fontSize: 11, color: "#475569" }}>нет теста</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Tab: Logs */}
      {(tab === "logs" || tab === "errors") && (
        <div>
          {tab === "logs" && (
            <div style={{ display: "flex", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
              <select
                value={filterIntegration}
                onChange={e => setFilterIntegration(e.target.value)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: 8, padding: "7px 12px", fontSize: 13 }}
              >
                <option value="">Все интеграции</option>
                {Object.entries({
                  merchrules: "Merchrules", airtable: "Airtable", ktalk: "KTalk",
                  tbank: "KTime/TBank", google_sheets: "Google Sheets", telegram: "Telegram",
                  jira: "Jira", extension: "Extension",
                }).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <select
                value={filterStatus}
                onChange={e => setFilterStatus(e.target.value)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", borderRadius: 8, padding: "7px 12px", fontSize: 13 }}
              >
                <option value="">Все статусы</option>
                <option value="ok">OK</option>
                <option value="success">Success</option>
                <option value="error">Error</option>
                <option value="warning">Warning</option>
                <option value="running">Running</option>
              </select>
              <button onClick={loadLogs} style={{ background: "#334155", border: "none", color: "#f8fafc", padding: "7px 14px", borderRadius: 8, cursor: "pointer", fontSize: 13 }}>
                ↻
              </button>
              <span style={{ fontSize: 12, color: "#475569", alignSelf: "center" }}>
                {logsLoading ? "Загрузка..." : `${logsTotal} записей`}
              </span>
            </div>
          )}

          {tab === "errors" && overview && (
            <div style={{ fontSize: 12, color: "#94a3b8", marginBottom: 12 }}>
              Последние {overview.recent_errors.length} ошибок синхронизации
            </div>
          )}

          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ color: "#64748b", textAlign: "left", borderBottom: "1px solid #334155" }}>
                  {["ID", "Интеграция", "Тип", "Действие", "Статус", "Записей", "Ошибок", "Время", "Длительность"].map(h => (
                    <th key={h} style={{ padding: "8px 10px", fontWeight: 600 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(tab === "errors" ? overview?.recent_errors : logs).map(log => (
                  <React.Fragment key={log.id}>
                    <tr
                      onClick={() => setExpandedLog(expandedLog === log.id ? null : log.id)}
                      style={{
                        borderBottom: "1px solid #1e293b", cursor: "pointer",
                        background: expandedLog === log.id ? "#1e293b" : "transparent",
                        transition: "background 0.1s",
                      }}
                    >
                      <td style={{ padding: "8px 10px", color: "#475569" }}>{log.id}</td>
                      <td style={{ padding: "8px 10px", fontWeight: 600, color: "#f8fafc" }}>{log.integration}</td>
                      <td style={{ padding: "8px 10px", color: "#94a3b8" }}>{log.resource_type || "—"}</td>
                      <td style={{ padding: "8px 10px", color: "#94a3b8" }}>{log.action || "—"}</td>
                      <td style={{ padding: "8px 10px" }}>{statusBadge(log.status)}</td>
                      <td style={{ padding: "8px 10px", color: "#94a3b8" }}>{log.records_processed}</td>
                      <td style={{ padding: "8px 10px", color: log.errors_count > 0 ? "#ef4444" : "#94a3b8" }}>{log.errors_count}</td>
                      <td style={{ padding: "8px 10px", color: "#64748b" }}>{fmtDt(log.started_at)}</td>
                      <td style={{ padding: "8px 10px", color: "#64748b" }}>{fmtDur(log.duration_ms)}</td>
                    </tr>
                    {expandedLog === log.id && (
                      <tr>
                        <td colSpan={9} style={{ padding: "0 10px 12px 10px", background: "#0f172a" }}>
                          <div style={{ padding: "12px", borderRadius: 8, border: "1px solid #334155" }}>
                            {log.message && (
                              <div style={{ marginBottom: 8 }}>
                                <span style={{ color: "#64748b", fontSize: 11 }}>Сообщение: </span>
                                <span style={{ color: "#f8fafc" }}>{log.message}</span>
                              </div>
                            )}
                            {log.sync_data && Object.keys(log.sync_data).length > 0 && (
                              <pre style={{
                                color: "#94a3b8", fontSize: 11, background: "#1e293b",
                                padding: "8px 12px", borderRadius: 6, overflow: "auto", maxHeight: 200,
                              }}>{JSON.stringify(log.sync_data, null, 2)}</pre>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
            {(tab === "logs" && !logsLoading && logs.length === 0) && (
              <div style={{ textAlign: "center", color: "#475569", padding: "40px 0" }}>Нет записей</div>
            )}
          </div>
        </div>
      )}

      {/* Tab: Audit */}
      {tab === "audit" && (
        <div>
          <div style={{ fontSize: 12, color: "#94a3b8", marginBottom: 12 }}>
            Последние 50 действий пользователей
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ color: "#64748b", textAlign: "left", borderBottom: "1px solid #334155" }}>
                  {["ID", "Пользователь", "Действие", "Тип ресурса", "ID ресурса", "IP", "Время"].map(h => (
                    <th key={h} style={{ padding: "8px 10px", fontWeight: 600 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {auditLogs.map(log => (
                  <tr key={log.id} style={{ borderBottom: "1px solid #1e293b" }}>
                    <td style={{ padding: "8px 10px", color: "#475569" }}>{log.id}</td>
                    <td style={{ padding: "8px 10px", color: "#94a3b8" }}>{log.user_id || "—"}</td>
                    <td style={{ padding: "8px 10px", color: "#f8fafc", fontWeight: 600 }}>{log.action}</td>
                    <td style={{ padding: "8px 10px", color: "#94a3b8" }}>{log.resource_type || "—"}</td>
                    <td style={{ padding: "8px 10px", color: "#64748b" }}>{log.resource_id || "—"}</td>
                    <td style={{ padding: "8px 10px", color: "#64748b" }}>{log.ip_address || "—"}</td>
                    <td style={{ padding: "8px 10px", color: "#64748b" }}>{fmtDt(log.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {auditLogs.length === 0 && (
              <div style={{ textAlign: "center", color: "#475569", padding: "40px 0" }}>Нет данных</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

window.PageDebug = PageDebug;
