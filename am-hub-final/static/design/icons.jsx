// icons.jsx — custom stroke icons (stroke-width 1.5)
// single-shape silhouettes, no fills. for the "signal" aesthetic.

const Icon = ({ d, size = 16, stroke = "currentColor", children, sw = 1.5, fill = "none", ...rest }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke={stroke}
       strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" {...rest}>
    {children || <path d={d} />}
  </svg>
);

// namespace of icons we actually use
const I = {
  command:   (p) => <Icon {...p}><path d="M5 9h4V5M15 5v4h4M19 15h-4v4M9 19v-4H5"/><rect x="9" y="9" width="6" height="6"/></Icon>,
  sun:       (p) => <Icon {...p}><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></Icon>,
  moon:      (p) => <Icon {...p}><path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/></Icon>,
  users:     (p) => <Icon {...p}><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/><circle cx="17" cy="9" r="2.5"/><path d="M16 14c3 0 5.5 2 5.5 5"/></Icon>,
  trophy:    (p) => <Icon {...p}><path d="M7 4h10v4a5 5 0 0 1-10 0V4zM7 6H4v2a3 3 0 0 0 3 3M17 6h3v2a3 3 0 0 1-3 3M10 14h4v3h-4zM8 20h8"/></Icon>,
  check:     (p) => <Icon {...p}><rect x="4" y="5" width="16" height="15" rx="1"/><path d="M8 12l3 3 5-6"/></Icon>,
  cal:       (p) => <Icon {...p}><rect x="3" y="5" width="18" height="16" rx="1"/><path d="M3 10h18M8 3v4M16 3v4"/></Icon>,
  folder:    (p) => <Icon {...p}><path d="M3 7a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7z"/></Icon>,
  chart:     (p) => <Icon {...p}><path d="M3 20h18M6 16V9M11 16V5M16 16v-4M21 16v-9"/></Icon>,
  bot:       (p) => <Icon {...p}><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M12 4v4M9 14h.01M15 14h.01M8 18h8"/></Icon>,
  kanban:    (p) => <Icon {...p}><rect x="3" y="4" width="5" height="16" rx="1"/><rect x="10" y="4" width="5" height="10" rx="1"/><rect x="17" y="4" width="4" height="13" rx="1"/></Icon>,
  target:    (p) => <Icon {...p}><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1"/></Icon>,
  map:       (p) => <Icon {...p}><path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2V6zM9 4v16M15 6v16"/></Icon>,
  doc:       (p) => <Icon {...p}><path d="M6 3h8l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM14 3v4h4M8 12h8M8 16h8M8 8h4"/></Icon>,
  spark:     (p) => <Icon {...p}><path d="M12 3l1.8 5.4L19 10l-5.2 1.6L12 17l-1.8-5.4L5 10l5.2-1.6L12 3z"/></Icon>,
  link:      (p) => <Icon {...p}><path d="M10 14a4 4 0 0 1 0-5.7l3-3a4 4 0 0 1 5.7 5.7L17 12.5M14 10a4 4 0 0 1 0 5.7l-3 3a4 4 0 0 1-5.7-5.7L7 11.5"/></Icon>,
  puzzle:    (p) => <Icon {...p}><path d="M10 4h4v3a2 2 0 1 0 0 4v4h-3a2 2 0 1 1-4 0H4V4h6zm10 7a2 2 0 1 1-4 0H14v4h6v-4z"/></Icon>,
  help:      (p) => <Icon {...p}><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 0 1 5 0c0 1.5-2.5 2-2.5 3.5M12 17h.01"/></Icon>,
  bell:      (p) => <Icon {...p}><path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4l2-2zM10 20a2 2 0 0 0 4 0"/></Icon>,
  search:    (p) => <Icon {...p}><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></Icon>,
  arrow_r:   (p) => <Icon {...p}><path d="M5 12h14M13 6l6 6-6 6"/></Icon>,
  arrow_l:   (p) => <Icon {...p}><path d="M19 12H5M11 6l-6 6 6 6"/></Icon>,
  arrow_up:  (p) => <Icon {...p}><path d="M7 14l5-5 5 5"/></Icon>,
  arrow_dn:  (p) => <Icon {...p}><path d="M7 10l5 5 5-5"/></Icon>,
  plus:      (p) => <Icon {...p}><path d="M12 5v14M5 12h14"/></Icon>,
  x:         (p) => <Icon {...p}><path d="M6 6l12 12M18 6L6 18"/></Icon>,
  gear:      (p) => <Icon {...p}><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/></Icon>,
  signout:   (p) => <Icon {...p}><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="M10 17l5-5-5-5M15 12H3"/></Icon>,
  play:      (p) => <Icon {...p}><path d="M7 4v16l13-8L7 4z"/></Icon>,
  dot3:      (p) => <Icon {...p}><circle cx="6" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18" cy="12" r="1.2"/></Icon>,
  flame:     (p) => <Icon {...p}><path d="M12 3c3 4 6 6 6 10a6 6 0 0 1-12 0c0-2 1-4 3-5-.5 2 .5 3 2 3 0-2-1-3 1-8z"/></Icon>,
  alert:     (p) => <Icon {...p}><path d="M12 3L2 21h20L12 3zM12 10v5M12 18h.01"/></Icon>,
  refresh:   (p) => <Icon {...p}><path d="M20 4v6h-6M4 20v-6h6"/><path d="M4 10a8 8 0 0 1 14-3M20 14a8 8 0 0 1-14 3"/></Icon>,
  filter:    (p) => <Icon {...p}><path d="M3 5h18l-7 9v6l-4-2v-4L3 5z"/></Icon>,
  cmd_k:     (p) => <Icon {...p}><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M9 10l-3 2 3 2M15 10l3 2-3 2M13 9l-2 6"/></Icon>,
  pin:       (p) => <Icon {...p}><path d="M12 2l3 5 5 1-4 4 1 5-5-3-5 3 1-5-4-4 5-1 3-5zM12 14v7"/></Icon>,
  lightning: (p) => <Icon {...p}><path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z"/></Icon>,
  eye:       (p) => <Icon {...p}><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></Icon>,
  download:  (p) => <Icon {...p}><path d="M12 3v13M6 12l6 6 6-6M4 21h16"/></Icon>,
  copy:      (p) => <Icon {...p}><rect x="8" y="8" width="12" height="12" rx="1"/><path d="M4 16V5a1 1 0 0 1 1-1h11"/></Icon>,
  mic:       (p) => <Icon {...p}><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></Icon>,
  video:     (p) => <Icon {...p}><rect x="3" y="6" width="13" height="12" rx="1"/><path d="M16 10l5-3v10l-5-3z"/></Icon>,
  chat:      (p) => <Icon {...p}><path d="M4 5h16v11H8l-4 4V5z"/></Icon>,
  lock:      (p) => <Icon {...p}><rect x="4" y="10" width="16" height="11" rx="1"/><path d="M8 10V7a4 4 0 1 1 8 0v3"/></Icon>,
  circle_check: (p) => <Icon {...p}><circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/></Icon>,
  circle_x:  (p) => <Icon {...p}><circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/></Icon>,
  circle_pause: (p) => <Icon {...p}><circle cx="12" cy="12" r="9"/><path d="M10 9v6M14 9v6"/></Icon>,
  grid:      (p) => <Icon {...p}><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></Icon>,
  logo: (p = {}) => (
    <svg width={p.size || 24} height={p.size || 24} viewBox="0 0 24 24" fill="none" {...p}>
      <rect x="1.5" y="1.5" width="21" height="21" rx="4" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M6 17l4-10 2 5 2-5 4 10" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
      <circle cx="12" cy="12" r="1.4" fill="currentColor"/>
    </svg>
  ),
};

window.I = I;
window.Icon = Icon;
