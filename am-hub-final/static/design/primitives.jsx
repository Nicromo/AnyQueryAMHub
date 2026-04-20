// primitives.jsx — shared small components

// ── Badge ─────────────────────────────────────────────────
const Badge = ({ tone = "neutral", children, dot = false, mono = true, style = {} }) => {
  const tones = {
    neutral: { bg: "var(--ink-3)", fg: "var(--ink-7)", bd: "var(--line)" },
    signal:  { bg: "color-mix(in oklch, var(--signal) 14%, transparent)", fg: "var(--signal)", bd: "color-mix(in oklch, var(--signal) 28%, transparent)" },
    critical:{ bg: "color-mix(in oklch, var(--critical) 14%, transparent)", fg: "var(--critical)", bd: "color-mix(in oklch, var(--critical) 35%, transparent)" },
    warn:    { bg: "color-mix(in oklch, var(--warn) 12%, transparent)", fg: "var(--warn)", bd: "color-mix(in oklch, var(--warn) 30%, transparent)" },
    info:    { bg: "color-mix(in oklch, var(--info) 12%, transparent)", fg: "var(--info)", bd: "color-mix(in oklch, var(--info) 30%, transparent)" },
    ok:      { bg: "color-mix(in oklch, var(--ok) 12%, transparent)", fg: "var(--ok)", bd: "color-mix(in oklch, var(--ok) 30%, transparent)" },
    ghost:   { bg: "transparent", fg: "var(--ink-6)", bd: "var(--line)" },
    solid:   { bg: "var(--signal)", fg: "var(--ink-0)", bd: "var(--signal)" },
  };
  const t = tones[tone] || tones.neutral;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "2px 7px",
      background: t.bg, color: t.fg,
      border: `1px solid ${t.bd}`,
      borderRadius: 3,
      fontFamily: mono ? "var(--f-mono)" : "var(--f-display)",
      fontSize: 10.5, fontWeight: 500,
      textTransform: "uppercase", letterSpacing: "0.06em",
      lineHeight: 1.3, whiteSpace: "nowrap",
      ...style,
    }}>
      {dot && <span style={{ width: 5, height: 5, borderRadius: 999, background: t.fg }}/>}
      {children}
    </span>
  );
};

// ── Button ────────────────────────────────────────────────
const Btn = ({ kind = "ghost", size = "m", children, icon, iconRight, full = false, onClick, style = {}, ...rest }) => {
  const sizes = {
    s: { h: 26, px: 10, fs: 12 },
    m: { h: 34, px: 14, fs: 13 },
    l: { h: 42, px: 18, fs: 14 },
  };
  const kinds = {
    primary: { bg: "var(--signal)", fg: "var(--ink-0)", bd: "var(--signal)" },
    ghost:   { bg: "transparent", fg: "var(--ink-8)", bd: "var(--line)" },
    dim:     { bg: "var(--ink-2)", fg: "var(--ink-7)", bd: "var(--line)" },
    danger:  { bg: "transparent", fg: "var(--critical)", bd: "color-mix(in oklch, var(--critical) 40%, transparent)" },
  };
  const s = sizes[size], k = kinds[kind];
  return (
    <button onClick={onClick} style={{
      display: "inline-flex", alignItems: "center", gap: 8,
      height: s.h, padding: `0 ${s.px}px`,
      background: k.bg, color: k.fg,
      border: `1px solid ${k.bd}`,
      borderRadius: 4,
      fontFamily: "var(--f-display)", fontSize: s.fs, fontWeight: 500,
      cursor: "pointer", letterSpacing: "-0.005em",
      transition: "all var(--dur) var(--ease)",
      width: full ? "100%" : undefined,
      justifyContent: full ? "center" : undefined,
      ...style,
    }} {...rest}>
      {icon}
      {children}
      {iconRight}
    </button>
  );
};

// ── Card ──────────────────────────────────────────────────
const Card = ({ title, action, children, style = {}, bodyStyle = {}, dense = false }) => (
  <section style={{
    background: "var(--ink-2)",
    border: "1px solid var(--line)",
    borderRadius: 6,
    ...style,
  }}>
    {title && (
      <header style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: dense ? "10px 14px" : "14px 18px",
        borderBottom: "1px solid var(--line-soft)",
      }}>
        <div style={{
          fontFamily: "var(--f-mono)", fontSize: 11, fontWeight: 500,
          color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.08em",
        }}>{title}</div>
        {action}
      </header>
    )}
    <div style={{ padding: dense ? 14 : 18, ...bodyStyle }}>{children}</div>
  </section>
);

// ── KPI ───────────────────────────────────────────────────
const KPI = ({ label, value, delta, tone = "neutral", sub, unit, big = false }) => {
  const tones = {
    neutral: "var(--ink-8)",
    signal: "var(--signal)",
    critical: "var(--critical)",
    warn: "var(--warn)",
    ok: "var(--ok)",
  };
  return (
    <div style={{
      padding: "16px 18px",
      background: "var(--ink-2)",
      border: "1px solid var(--line)",
      borderRadius: 6,
      minWidth: 0,
      display: "flex", flexDirection: "column", gap: 8,
    }}>
      <div style={{
        fontFamily: "var(--f-mono)", fontSize: 10.5, fontWeight: 500,
        color: "var(--ink-6)", textTransform: "uppercase", letterSpacing: "0.09em",
      }}>{label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <div style={{
          fontFamily: "var(--f-display)",
          fontSize: big ? 44 : 34, fontWeight: 500,
          color: tones[tone], letterSpacing: "-0.03em", lineHeight: 1,
          fontVariantNumeric: "tabular-nums",
        }}>{value}</div>
        {unit && <div className="mono" style={{ fontSize: 12, color: "var(--ink-6)" }}>{unit}</div>}
      </div>
      {(delta || sub) && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11.5 }}>
          {delta && (
            <span className="mono" style={{
              color: delta.startsWith("-") ? "var(--critical)" : "var(--ok)",
              fontWeight: 500,
            }}>{delta}</span>
          )}
          {sub && <span style={{ color: "var(--ink-6)" }}>{sub}</span>}
        </div>
      )}
    </div>
  );
};

// ── Sparkline ─────────────────────────────────────────────
const Spark = ({ data = [], w = 90, h = 26, color = "var(--signal)", fill = true }) => {
  if (!data.length) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const rng = max - min || 1;
  const pts = data.map((v, i) => [
    (i / (data.length - 1)) * w,
    h - ((v - min) / rng) * (h - 2) - 1,
  ]);
  const path = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = fill ? path + ` L ${w} ${h} L 0 ${h} Z` : null;
  return (
    <svg width={w} height={h} style={{ display: "block" }}>
      {fill && <path d={area} fill={color} fillOpacity="0.12" stroke="none"/>}
      <path d={path} fill="none" stroke={color} strokeWidth="1.25" strokeLinejoin="round"/>
    </svg>
  );
};

// ── Segment pill (small classifier) ───────────────────────
const Seg = ({ value }) => {
  const v = (value || "").toUpperCase();
  const map = {
    "A+": "signal", "A": "signal",
    "B": "info", "B+": "info",
    "C": "neutral",
    "D": "warn",
    "NEW": "ok",
  };
  return <Badge tone={map[v] || "neutral"}>{v || "—"}</Badge>;
};

// ── Stat dot with label ───────────────────────────────────
const StatDot = ({ tone = "ok", children }) => {
  const colors = { ok: "var(--ok)", warn: "var(--warn)", critical: "var(--critical)", info: "var(--info)", signal: "var(--signal)" };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--ink-7)", fontSize: 12 }}>
      <span style={{ width: 6, height: 6, borderRadius: 999, background: colors[tone], boxShadow: `0 0 8px ${colors[tone]}` }}/>
      {children}
    </span>
  );
};

// ── Placeholder avatar (initials) ─────────────────────────
const Avatar = ({ name = "", size = 24, tone = "signal" }) => {
  const initials = name.split(" ").map(s => s[0]).filter(Boolean).slice(0, 2).join("").toUpperCase() || "?";
  return (
    <span style={{
      width: size, height: size, borderRadius: 999,
      background: tone === "signal" ? "color-mix(in oklch, var(--signal) 20%, var(--ink-2))" : "var(--ink-3)",
      color: tone === "signal" ? "var(--signal)" : "var(--ink-8)",
      border: "1px solid var(--line)",
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      fontFamily: "var(--f-mono)", fontSize: size * 0.38, fontWeight: 600,
      letterSpacing: 0,
      flexShrink: 0,
    }}>{initials}</span>
  );
};

// ── Progress bar ──────────────────────────────────────────
const Progress = ({ value = 0, max = 100, tone = "signal", h = 4, showLabel = false }) => {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const colors = { signal: "var(--signal)", critical: "var(--critical)", warn: "var(--warn)", ok: "var(--ok)" };
  return (
    <div>
      <div style={{
        height: h, background: "var(--ink-3)", borderRadius: 999, overflow: "hidden",
      }}>
        <div style={{
          width: `${pct}%`, height: "100%",
          background: colors[tone],
          borderRadius: 999,
          transition: "width 400ms var(--ease)",
        }}/>
      </div>
      {showLabel && (
        <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-6)", marginTop: 4 }}>
          {Math.round(pct)}%
        </div>
      )}
    </div>
  );
};

// ── Kbd ───────────────────────────────────────────────────
const Kbd = ({ children }) => (
  <span style={{
    display: "inline-flex", alignItems: "center", justifyContent: "center",
    minWidth: 18, height: 18, padding: "0 5px",
    background: "var(--ink-3)", border: "1px solid var(--line)",
    borderRadius: 3, fontFamily: "var(--f-mono)",
    fontSize: 10.5, color: "var(--ink-7)", fontWeight: 500,
  }}>{children}</span>
);

// ── Image placeholder (striped) ───────────────────────────
const Placeholder = ({ label = "placeholder", w = "100%", h = 120 }) => (
  <div style={{
    width: w, height: h,
    background: "repeating-linear-gradient(135deg, var(--ink-2) 0 6px, var(--ink-3) 6px 12px)",
    border: "1px dashed var(--line)",
    borderRadius: 4,
    display: "flex", alignItems: "center", justifyContent: "center",
    fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--ink-5)",
  }}>{label}</div>
);

// ── Confirm / Toast — заменяют window.confirm/alert на внутренние модалки ────

function _ensureOverlay() {
  let root = document.getElementById("__app_overlay_root");
  if (!root) {
    root = document.createElement("div");
    root.id = "__app_overlay_root";
    document.body.appendChild(root);
  }
  return root;
}

// Промис-основанный confirm. await appConfirm("Текст?") → true/false
function appConfirm(message, opts) {
  const { title = "Подтверждение", okLabel = "OK", cancelLabel = "Отмена", tone = "primary" } = opts || {};
  return new Promise((resolve) => {
    const root = _ensureOverlay();
    const wrap = document.createElement("div");
    wrap.style.cssText = "position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.55);backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;padding:24px;";
    wrap.innerHTML = `
      <style>
        .ac-card { background: var(--ink-1); border: 1px solid var(--line); border-radius: 10px;
          max-width: 440px; width: 100%; padding: 22px 24px; box-shadow: 0 24px 64px rgba(0,0,0,.5); }
        .ac-title { font-size: 15px; font-weight: 600; color: var(--ink-9); margin-bottom: 8px; }
        .ac-msg { font-size: 13px; color: var(--ink-7); line-height: 1.5; white-space: pre-wrap; margin-bottom: 18px; }
        .ac-actions { display: flex; justify-content: flex-end; gap: 8px; }
        .ac-btn { padding: 8px 16px; font-size: 12.5px; font-weight: 500; border-radius: 4px;
          border: 1px solid var(--line); background: var(--ink-2); color: var(--ink-8); cursor: pointer; }
        .ac-btn:hover { background: var(--ink-3); }
        .ac-btn-primary { background: var(--signal); border-color: var(--signal); color: var(--ink-0); }
        .ac-btn-danger { background: var(--critical); border-color: var(--critical); color: #fff; }
      </style>
      <div class="ac-card" role="dialog" aria-modal="true">
        <div class="ac-title"></div>
        <div class="ac-msg"></div>
        <div class="ac-actions">
          <button class="ac-btn" data-act="cancel"></button>
          <button class="ac-btn ${tone === "danger" ? "ac-btn-danger" : "ac-btn-primary"}" data-act="ok"></button>
        </div>
      </div>`;
    wrap.querySelector(".ac-title").textContent = title;
    wrap.querySelector(".ac-msg").textContent = String(message || "");
    wrap.querySelector('[data-act="ok"]').textContent = okLabel;
    wrap.querySelector('[data-act="cancel"]').textContent = cancelLabel;
    const close = (v) => { wrap.remove(); document.removeEventListener("keydown", onKey); resolve(v); };
    wrap.querySelector('[data-act="ok"]').onclick = () => close(true);
    wrap.querySelector('[data-act="cancel"]').onclick = () => close(false);
    wrap.onclick = (e) => { if (e.target === wrap) close(false); };
    const onKey = (e) => { if (e.key === "Escape") close(false); if (e.key === "Enter") close(true); };
    document.addEventListener("keydown", onKey);
    root.appendChild(wrap);
    setTimeout(() => wrap.querySelector('[data-act="ok"]').focus(), 50);
  });
}

// Тост — appToast(msg) / appToast(msg, "error") / appToast(msg, {tone, duration})
function appToast(message, toneOrOpts) {
  let tone = "info", duration = 3500;
  if (typeof toneOrOpts === "string") tone = toneOrOpts;
  else if (toneOrOpts) { tone = toneOrOpts.tone || tone; duration = toneOrOpts.duration || duration; }
  let stack = document.getElementById("__app_toast_stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.id = "__app_toast_stack";
    stack.style.cssText = "position:fixed;bottom:24px;right:24px;z-index:10000;display:flex;flex-direction:column;gap:8px;pointer-events:none;";
    document.body.appendChild(stack);
  }
  const colors = { info: "var(--signal)", ok: "var(--ok)", error: "var(--critical)", warn: "var(--warn)" };
  const toast = document.createElement("div");
  toast.style.cssText = `padding:12px 16px;background:var(--ink-1);border:1px solid var(--line);border-left:3px solid ${colors[tone] || colors.info};border-radius:6px;color:var(--ink-8);font-size:12.5px;box-shadow:0 8px 24px rgba(0,0,0,.4);max-width:420px;white-space:pre-wrap;pointer-events:auto;`;
  toast.textContent = String(message || "");
  stack.appendChild(toast);
  setTimeout(() => {
    toast.style.transition = "opacity .25s ease";
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// Промис-осн. ввод текста. await appPrompt("Заметка:") → string | null
function appPrompt(message, opts) {
  const {
    title = "Введите значение",
    okLabel = "OK", cancelLabel = "Отмена",
    placeholder = "", multiline = true,
    defaultValue = "",
  } = opts || {};
  return new Promise((resolve) => {
    const root = _ensureOverlay();
    const wrap = document.createElement("div");
    wrap.style.cssText = "position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.55);backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;padding:24px;";
    const inputHtml = multiline
      ? '<textarea class="ap-input" rows="5" style="width:100%;resize:vertical;min-height:110px;background:var(--ink-2);color:var(--ink-8);border:1px solid var(--line);border-radius:4px;padding:8px 10px;font-size:13px;font-family:inherit;"></textarea>'
      : '<input type="text" class="ap-input" style="width:100%;background:var(--ink-2);color:var(--ink-8);border:1px solid var(--line);border-radius:4px;padding:8px 10px;font-size:13px;font-family:inherit;"/>';
    wrap.innerHTML =
      '<div class="ac-card" role="dialog" aria-modal="true" style="background:var(--ink-1);border:1px solid var(--line);border-radius:10px;max-width:560px;width:100%;padding:22px 24px;box-shadow:0 24px 64px rgba(0,0,0,.5);">' +
      '<div class="ac-title" style="font-size:15px;font-weight:600;color:var(--ink-9);margin-bottom:6px;"></div>' +
      '<div class="ac-msg" style="font-size:12.5px;color:var(--ink-7);line-height:1.5;margin-bottom:10px;"></div>' +
      inputHtml +
      '<div class="ac-actions" style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px;">' +
      '<button class="ac-btn" data-act="cancel" style="padding:8px 16px;font-size:12.5px;font-weight:500;border-radius:4px;border:1px solid var(--line);background:var(--ink-2);color:var(--ink-8);cursor:pointer;"></button>' +
      '<button class="ac-btn ac-btn-primary" data-act="ok" style="padding:8px 16px;font-size:12.5px;font-weight:500;border-radius:4px;border:1px solid var(--signal);background:var(--signal);color:var(--ink-0);cursor:pointer;"></button>' +
      '</div></div>';
    wrap.querySelector(".ac-title").textContent = title;
    wrap.querySelector(".ac-msg").textContent = String(message || "");
    const input = wrap.querySelector(".ap-input");
    input.placeholder = placeholder;
    input.value = defaultValue;
    wrap.querySelector('[data-act="ok"]').textContent = okLabel;
    wrap.querySelector('[data-act="cancel"]').textContent = cancelLabel;
    const close = (v) => { wrap.remove(); document.removeEventListener("keydown", onKey); resolve(v); };
    wrap.querySelector('[data-act="ok"]').onclick = () => close(input.value);
    wrap.querySelector('[data-act="cancel"]').onclick = () => close(null);
    wrap.onclick = (e) => { if (e.target === wrap) close(null); };
    const onKey = (e) => {
      if (e.key === "Escape") close(null);
      if (e.key === "Enter" && !multiline) close(input.value);
      if (e.key === "Enter" && multiline && (e.ctrlKey || e.metaKey)) close(input.value);
    };
    document.addEventListener("keydown", onKey);
    root.appendChild(wrap);
    setTimeout(() => input.focus(), 50);
  });
}

// ── TaskCheck (styled checkbox replacement) ───────────────
const TaskCheck = ({ checked = false, onChange, size = 18, disabled = false }) => {
  return React.createElement("button", {
    type: "button", disabled,
    onClick: (e) => { e.stopPropagation(); if (!disabled && onChange) onChange(!checked); },
    "aria-checked": checked ? "true" : "false",
    role: "checkbox",
    style: {
      width: size, height: size,
      background: checked ? "var(--signal)" : "transparent",
      border: `1.5px solid ${checked ? "var(--signal)" : "var(--ink-5)"}`,
      borderRadius: 4,
      cursor: disabled ? "not-allowed" : "pointer",
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      padding: 0, flexShrink: 0,
      transition: "all var(--dur,.15s) var(--ease,ease)",
    },
  },
    checked && React.createElement("svg", {
      width: size - 6, height: size - 6, viewBox: "0 0 12 12", fill: "none", "aria-hidden": "true",
    },
      React.createElement("path", {
        d: "M2.5 6.5 L5 9 L10 3",
        stroke: "var(--ink-0)", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round",
      }),
    ),
  );
};

// ── TaskSnoozeButton — dropdown «Отложить» для автотасок ───────────────────
// POST /api/tasks/{id}/snooze {days}. onSnoozed() — колбэк для перезагрузки списка.
function TaskSnoozeButton({ taskId, onSnoozed, compact = false }) {
  const [open, setOpen] = React.useState(false);
  const choose = async (days) => {
    setOpen(false);
    try {
      const r = await fetch(`/api/tasks/${taskId}/snooze`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days }),
      });
      if (r.ok) { onSnoozed && onSnoozed(); window.appToast && window.appToast(`Отложено на ${days} д.`); }
    } catch (e) { /* swallow */ }
  };
  const customDays = async () => {
    const d = window.prompt ? window.prompt("На сколько дней отложить?", "5") : "5";
    const n = parseInt(d, 10);
    if (!n || n < 1 || n > 90) return;
    await choose(n);
  };
  return React.createElement("div", { style: { position: "relative", display: "inline-block" } },
    React.createElement("button", {
      onClick: (e) => { e.stopPropagation(); setOpen(!open); },
      title: "Отложить задачу",
      style: {
        height: compact ? 24 : 28, padding: compact ? "0 8px" : "0 10px",
        background: "transparent", border: "1px solid var(--line)", borderRadius: 4,
        color: "var(--ink-6)", cursor: "pointer", fontSize: 11,
      },
    }, "⏱ Отложить"),
    open && React.createElement("div", {
      style: {
        position: "absolute", top: (compact ? 26 : 30), right: 0, minWidth: 140,
        background: "var(--ink-1)", border: "1px solid var(--line)", borderRadius: 6,
        boxShadow: "0 6px 16px rgba(0,0,0,.18)", zIndex: 60,
      },
      onClick: (e) => e.stopPropagation(),
    },
      [1, 3, 7, 14].map(d => React.createElement("div", {
        key: d, onClick: () => choose(d),
        style: { padding: "7px 12px", fontSize: 12.5, color: "var(--ink-8)", cursor: "pointer" },
      }, `+${d} ${d === 1 ? "день" : (d < 5 ? "дня" : "дней")}`)),
      React.createElement("div", {
        onClick: customDays,
        style: {
          padding: "7px 12px", fontSize: 12.5, color: "var(--ink-7)",
          cursor: "pointer", borderTop: "1px solid var(--line-soft)",
        },
      }, "Другое…"),
    ),
  );
}

Object.assign(window, { Badge, Btn, Card, KPI, Spark, Seg, StatDot, Avatar, Progress, Kbd, Placeholder, appConfirm, appToast, appPrompt, TaskCheck, TaskSnoozeButton });
