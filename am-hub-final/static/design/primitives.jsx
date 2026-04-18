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

Object.assign(window, { Badge, Btn, Card, KPI, Spark, Seg, StatDot, Avatar, Progress, Kbd, Placeholder });
