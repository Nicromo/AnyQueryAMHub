import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";

type FilterPreset = {
  id: string;
  name: string;
  siteId: string;
  q?: string;
  initiator?: string;
};

type ColumnFilters = {
  onlyMyCases: boolean;
  includeResolved: boolean;
  siteId: string;
  q: string;
};

const LEGACY_PRESETS_KEY = "tcb_filter_presets";

function presetsStorageKey(accountId: string | null | undefined): string {
  const a = accountId && accountId.trim() ? accountId.trim() : "none";
  return `tcb_filter_presets_v2_${a}`;
}

function loadPresetsForAccount(accountId: string | null | undefined): FilterPreset[] {
  const key = presetsStorageKey(accountId);
  try {
    const raw = localStorage.getItem(key);
    if (raw) return JSON.parse(raw) as FilterPreset[];
  } catch {
    /* fall through */
  }
  try {
    const legacy = localStorage.getItem(LEGACY_PRESETS_KEY);
    if (legacy) return JSON.parse(legacy) as FilterPreset[];
  } catch {
    /* ignore */
  }
  return [];
}

function savePresetsForAccount(accountId: string | null | undefined, presets: FilterPreset[]) {
  localStorage.setItem(presetsStorageKey(accountId), JSON.stringify(presets));
}

function columnFiltersStorageKey(accountId: string | null | undefined): string {
  const a = accountId && accountId.trim() ? accountId.trim() : "none";
  return `tcb_column_filters_${a}`;
}

function normalizeColumnFilters(f: Partial<ColumnFilters> & { initiator?: string }): ColumnFilters {
  return {
    onlyMyCases: !!f.onlyMyCases,
    includeResolved: f.includeResolved !== false,
    siteId: f.siteId || "",
    q: f.q || "",
  };
}

function loadColumnFiltersFromStorage(accountId: string | null | undefined): Map<string, ColumnFilters> {
  try {
    const raw = localStorage.getItem(columnFiltersStorageKey(accountId));
    if (!raw) return new Map();
    const o = JSON.parse(raw) as Record<string, Partial<ColumnFilters> & { initiator?: string }>;
    const m = new Map<string, ColumnFilters>();
    for (const [k, v] of Object.entries(o)) {
      m.set(k, normalizeColumnFilters(v || {}));
    }
    return m;
  } catch {
    return new Map();
  }
}

function saveColumnFiltersToStorage(accountId: string | null | undefined, m: Map<string, ColumnFilters>) {
  const o: Record<string, ColumnFilters> = {};
  m.forEach((v, k) => {
    o[k] = v;
  });
  localStorage.setItem(columnFiltersStorageKey(accountId), JSON.stringify(o));
}

type AuthStatus = {
  personal_token_configured: boolean;
  oauth_configured: boolean;
  logged_in: boolean;
  username: string | null;
  account_id: string | null;
  active_account_id: string | null;
  multi_account_switch_disabled: boolean;
};

type LocalAccountRow = {
  account_id: string;
  username: string | null;
  file: string;
  legacy?: boolean;
};

type ColumnChannelTarget = { team_name: string; channel_name: string; channel_id: string };

type Column = {
  id: string;
  title: string;
  team_name: string;
  channel_name: string;
  channel_id: string;
  channels?: ColumnChannelTarget[];
  channels_json?: string;
  position: number;
  rules_json: string;
};

function columnChannelLabel(col: Column): string {
  const ch = col.channels;
  if (ch && ch.length > 0) {
    return ch.map((x) => `#${x.channel_name}`).join(" · ");
  }
  return col.channel_name ? `#${col.channel_name}` : "—";
}

const TIME_WEB_BASE = "https://time.tbank.ru";

/** Полные URL каналов для редактирования (как в модалке «Новая колонка»). */
function columnUrlsForEdit(col: Column): string {
  const ch = col.channels?.length ? col.channels : null;
  if (ch && ch.length > 0) {
    return ch.map((t) => `${TIME_WEB_BASE}/${t.team_name}/channels/${t.channel_name}`).join("\n");
  }
  return `${TIME_WEB_BASE}/${col.team_name}/channels/${col.channel_name}`;
}

type CaseRow = {
  id: string;
  column_id: string;
  site_id: string | null;
  assignee_raw: string | null;
  title_preview: string | null;
  permalink: string;
  status: string;
  initiator: string;
  /** TiMe user_id автора корневого поста — для «только мои треды» без поиска @ в тексте */
  root_author_user_id?: string;
  thread_search_text?: string;
  last_activity_at_ms: number;
  last_message_username: string;
  last_message_preview: string;
};

type JiraStatus = {
  configured: boolean;
  jira_host: string | null;
  user_hint: string | null;
  /** Пользователь, чей токен (Jira Cloud) */
  jira_account_id?: string | null;
  jira_display_name?: string | null;
  jira_name?: string | null;
};

type JiraIssueRow = {
  key: string;
  project_key: string;
  summary: string;
  description: string;
  status: string;
  priority: string;
  updated: string;
  browse_url: string;
  last_comment_author: string;
  last_comment_author_account_id?: string;
  last_comment_author_name?: string;
  last_comment_preview: string;
  last_comment_created: string;
  sort_timestamp: string;
};

type JiraBoardPrefs = {
  /** Скрыть задачи, где последний комментарий оставлен с этой учётки (по токену). */
  hideLastCommentMine: boolean;
  /** Сначала задачи, где последний комментарий не ваш; внутри группы — по дате (коммент / updated). */
  prioritizeOthersLastComment: boolean;
};

type JiraIssuesResponse = {
  issues: JiraIssueRow[];
  total: number;
  start_at: number;
  max_results: number;
  loaded_count?: number;
  jql_used: string;
};

type JiraColumnDef = {
  id: string;
  title: string;
  /** Пустой список = колонка «остальное» (все проекты, не попавшие в другие колонки) */
  projectKeys: string[];
  position: number;
};

type JiraColFilters = {
  siteId: string;
  q: string;
};

const DEFAULT_JIRA_COLUMNS: JiraColumnDef[] = [
  { id: "default-all", title: "Все задачи", projectKeys: [], position: 0 },
];

const JIRA_JQLS: Record<string, string> = {
  assigned: "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC",
  created: "reporter = currentUser() AND statusCategory != Done ORDER BY updated DESC",
  watching: "watcher = currentUser() AND statusCategory != Done ORDER BY updated DESC",
};

function jiraStorageAccount(accountId: string | null | undefined): string {
  return accountId && accountId.trim() ? accountId.trim() : "none";
}

const JIRA_COL_WIDTH_KEY = "tcb_jira_col_width_v1";

function loadJiraColWidthPx(): number {
  try {
    const v = parseInt(localStorage.getItem(JIRA_COL_WIDTH_KEY) || "", 10);
    if (Number.isFinite(v) && v >= 280 && v <= 720) return v;
  } catch {
    /* ignore */
  }
  return 440;
}

const TIME_COL_WIDTH_KEY = "tcb_time_col_width_v1";

function loadTimeColWidthPx(): number {
  try {
    const v = parseInt(localStorage.getItem(TIME_COL_WIDTH_KEY) || "", 10);
    if (Number.isFinite(v) && v >= 240 && v <= 720) return v;
  } catch {
    /* ignore */
  }
  return 320;
}

function loadJiraColumns(accountId: string | null | undefined): JiraColumnDef[] {
  try {
    const raw = localStorage.getItem(`tcb_jira_columns_v1_${jiraStorageAccount(accountId)}`);
    if (raw) {
      const arr = JSON.parse(raw) as JiraColumnDef[];
      if (Array.isArray(arr) && arr.length > 0) return arr;
    }
  } catch {
    /* ignore */
  }
  return [...DEFAULT_JIRA_COLUMNS];
}

function saveJiraColumns(accountId: string | null | undefined, cols: JiraColumnDef[]) {
  localStorage.setItem(`tcb_jira_columns_v1_${jiraStorageAccount(accountId)}`, JSON.stringify(cols));
}

function loadJiraColFilters(accountId: string | null | undefined): Map<string, JiraColFilters> {
  try {
    const raw = localStorage.getItem(`tcb_jira_col_filters_v1_${jiraStorageAccount(accountId)}`);
    if (!raw) return new Map();
    const o = JSON.parse(raw) as Record<string, JiraColFilters>;
    return new Map(Object.entries(o));
  } catch {
    return new Map();
  }
}

function saveJiraColFilters(accountId: string | null | undefined, m: Map<string, JiraColFilters>) {
  const o: Record<string, JiraColFilters> = {};
  m.forEach((v, k) => {
    o[k] = v;
  });
  localStorage.setItem(`tcb_jira_col_filters_v1_${jiraStorageAccount(accountId)}`, JSON.stringify(o));
}

const JIRA_BOARD_PREFS_KEY = "tcb_jira_board_prefs_v1";

function loadJiraBoardPrefs(accountId: string | null | undefined): JiraBoardPrefs {
  try {
    const raw = localStorage.getItem(`${JIRA_BOARD_PREFS_KEY}_${jiraStorageAccount(accountId)}`);
    if (raw) {
      const o = JSON.parse(raw) as Partial<JiraBoardPrefs>;
      return {
        hideLastCommentMine: !!o.hideLastCommentMine,
        prioritizeOthersLastComment: o.prioritizeOthersLastComment !== false,
      };
    }
  } catch {
    /* ignore */
  }
  return { hideLastCommentMine: false, prioritizeOthersLastComment: true };
}

function saveJiraBoardPrefs(accountId: string | null | undefined, p: JiraBoardPrefs) {
  localStorage.setItem(`${JIRA_BOARD_PREFS_KEY}_${jiraStorageAccount(accountId)}`, JSON.stringify(p));
}

function jiraStatusHasIdentity(st: JiraStatus | null): boolean {
  if (!st?.configured) return false;
  return Boolean(
    (st.jira_account_id && st.jira_account_id.trim()) ||
      (st.jira_display_name && st.jira_display_name.trim()) ||
      (st.jira_name && st.jira_name.trim()),
  );
}

function jiraLastCommentIsMine(row: JiraIssueRow, st: JiraStatus | null): boolean {
  if (!jiraStatusHasIdentity(st)) return false;
  const acc = (row.last_comment_author_account_id || "").trim();
  const myAcc = (st!.jira_account_id || "").trim();
  if (acc && myAcc && acc === myAcc) return true;
  const la = (row.last_comment_author || "").trim().toLowerCase();
  const ln = (row.last_comment_author_name || "").trim().toLowerCase();
  const myDn = (st!.jira_display_name || "").trim().toLowerCase();
  const myNm = (st!.jira_name || "").trim().toLowerCase();
  if (la && myDn && la === myDn) return true;
  if (la && myNm && la === myNm) return true;
  if (ln && myDn && ln === myDn) return true;
  if (ln && myNm && ln === myNm) return true;
  return false;
}

/** Точные ключи проектов для JQL (без префиксов со звёздочкой). */
function unionExactJiraProjectKeysForJql(cols: JiraColumnDef[]): string[] {
  const s = new Set<string>();
  for (const c of cols) {
    for (const k of c.projectKeys) {
      const u = k.trim().toUpperCase();
      if (u && !u.endsWith("*")) s.add(u);
    }
  }
  return [...s];
}

/** Хотя бы в одной колонке есть префикс вида «БУКВЫ*» — в JQL нельзя перечислить все проекты, тянем всю выборку. */
function anyJiraColumnHasPrefixWildcard(cols: JiraColumnDef[]): boolean {
  return cols.some((c) =>
    c.projectKeys.some((k) => {
      const u = k.trim().toUpperCase();
      return u.endsWith("*") && u.length > 1;
    }),
  );
}

function hasCatchallColumn(cols: JiraColumnDef[]): boolean {
  return cols.some((c) => c.projectKeys.length === 0);
}

function jiraProjectKeyMatchesSpec(projectKey: string, specRaw: string): boolean {
  const pk = (projectKey || "").toUpperCase();
  const spec = specRaw.trim().toUpperCase();
  if (!spec || !pk) return false;
  if (spec.endsWith("*")) {
    const prefix = spec.slice(0, -1);
    return prefix.length > 0 && pk.startsWith(prefix);
  }
  if (pk === spec) return true;
  // Голый ключ без *: как префикс (AAR → AAR, AARP, AARO). Иначе колонка «AAR» пуста,
  // если в инстансе нет проекта с ключом ровно AAR. Коллизия AIM / AIMP: колонку с более
  // длинным ключом держите левее в порядке колонок.
  return pk.length > spec.length && pk.startsWith(spec);
}

/** Сначала точные ключи, затем более длинные префиксы — чтобы AARW* раньше A*. */
function sortJiraKeySpecsForMatch(specs: string[]): string[] {
  const copy = [...specs].map((s) => s.trim()).filter(Boolean);
  return copy.sort((a, b) => {
    const au = a.toUpperCase();
    const bu = b.toUpperCase();
    const aw = au.endsWith("*");
    const bw = bu.endsWith("*");
    if (aw !== bw) return aw ? 1 : -1;
    const al = aw ? au.slice(0, -1).length : au.length;
    const bl = bw ? bu.slice(0, -1).length : bu.length;
    return bl - al;
  });
}

function buildBoardJql(mode: "assigned" | "created" | "watching", cols: JiraColumnDef[]): string {
  const base = JIRA_JQLS[mode];
  if (hasCatchallColumn(cols)) return base;
  if (anyJiraColumnHasPrefixWildcard(cols)) return base;
  const keys = unionExactJiraProjectKeysForJql(cols);
  if (keys.length === 0) return base;
  const orderIdx = base.toUpperCase().indexOf("ORDER BY");
  const clause = ` AND project in (${keys.join(", ")}) `;
  if (orderIdx === -1) return base + clause;
  return base.slice(0, orderIdx) + clause + base.slice(orderIdx);
}

function assignIssueToColumnId(issue: JiraIssueRow, cols: JiraColumnDef[]): string | null {
  const ordered = [...cols].sort((a, b) => a.position - b.position);
  if (ordered.length === 0) return null;
  const onlyCatchall = ordered.every((c) => c.projectKeys.length === 0);
  if (onlyCatchall) return ordered[0].id;
  const pk = (issue.project_key || issue.key.split("-")[0] || "").toUpperCase();
  for (const col of ordered) {
    const raw = col.projectKeys.map((k) => k.trim()).filter(Boolean);
    if (raw.length === 0) continue;
    for (const spec of sortJiraKeySpecsForMatch(raw)) {
      if (jiraProjectKeyMatchesSpec(pk, spec)) return col.id;
    }
  }
  const catchall = ordered.find((c) => c.projectKeys.length === 0);
  return catchall?.id ?? null;
}

function jiraIssueSortMs(row: JiraIssueRow): number {
  const t = row.sort_timestamp || row.last_comment_created || row.updated;
  const ms = Date.parse(t);
  return Number.isFinite(ms) ? ms : 0;
}

function jiraCompareIssues(
  a: JiraIssueRow,
  b: JiraIssueRow,
  st: JiraStatus | null,
  prioritizeOthers: boolean,
): number {
  if (prioritizeOthers && jiraStatusHasIdentity(st)) {
    const am = jiraLastCommentIsMine(a, st);
    const bm = jiraLastCommentIsMine(b, st);
    if (am !== bm) return (am ? 1 : 0) - (bm ? 1 : 0);
  }
  return jiraIssueSortMs(b) - jiraIssueSortMs(a);
}

function textHasSiteId(blob: string, sid: string): boolean {
  const s = sid.trim();
  if (!s) return true;
  try {
    const re = new RegExp(`(?<!\\d)${s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?!\\d)`);
    return re.test(blob);
  } catch {
    return blob.includes(s);
  }
}

function filterJiraColumnIssues(rows: JiraIssueRow[], f: JiraColFilters): JiraIssueRow[] {
  let out = [...rows];
  if (f.siteId.trim()) {
    const ids = f.siteId
      .split(/[,\s]+/)
      .map((x) => x.trim())
      .filter(Boolean);
    out = out.filter((row) => {
      const blob = `${row.summary}\n${row.description || ""}`;
      return ids.some((sid) => textHasSiteId(blob, sid));
    });
  }
  if (f.q.trim()) {
    const ql = f.q.toLowerCase();
    out = out.filter(
      (row) =>
        row.key.toLowerCase().includes(ql) ||
        (row.summary || "").toLowerCase().includes(ql) ||
        (row.description || "").toLowerCase().includes(ql) ||
        (row.last_comment_preview || "").toLowerCase().includes(ql) ||
        (row.last_comment_author || "").toLowerCase().includes(ql)
    );
  }
  return out;
}

function relTime(ms: number): string {
  if (!ms) return "—";
  const diff = Date.now() - ms;
  const m = Math.floor(diff / 60000);
  if (m < 1) return "только что";
  if (m < 60) return `${m} мин назад`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} ч назад`;
  const d = Math.floor(h / 24);
  return `${d} дн назад`;
}

function initiatorLabel(v: string): string {
  if (v === "self") return "Я инициатор";
  if (v === "incoming") return "Ко мне";
  return "Не указан инициатор";
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || r.statusText);
  }
  if (r.status === 204) return undefined as T;
  return r.json() as Promise<T>;
}

type JiraTransition = { id: string; name: string };
type JiraUser = { name: string; displayName: string; accountId: string };
type NotifSettings = {
  time_channel_url: string;
  time_channel_id: string;
  enabled: boolean;
  notify_new_comments: boolean;
  poll_interval_sec: number;
};
type JiraWatcherRow = {
  id: string;
  label: string;
  jql: string;
  watcher_type: string;
  enabled: boolean;
  position: number;
};

export default function App() {
  const [mainTab, setMainTab] = useState<"time" | "jira">("time");
  const [jiraStatus, setJiraStatus] = useState<JiraStatus | null>(null);
  const [jiraIssues, setJiraIssues] = useState<JiraIssueRow[]>([]);
  const [jiraTotal, setJiraTotal] = useState(0);
  const [jiraJql, setJiraJql] = useState("");
  const [jiraLoading, setJiraLoading] = useState(false);
  const [jiraPingOk, setJiraPingOk] = useState<string | null>(null);

  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [columns, setColumns] = useState<Column[]>([]);
  const [cases, setCases] = useState<CaseRow[]>([]);
  const [q, setQ] = useState("");
  const [siteId, setSiteId] = useState("");
  const [initiator, setInitiator] = useState<string>("");
  const [includeResolved, setIncludeResolved] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [historySyncing, setHistorySyncing] = useState(false);
  const [historyPagesInput, setHistoryPagesInput] = useState("50");
  const [historyReset, setHistoryReset] = useState(false);

  const [presets, setPresets] = useState<FilterPreset[]>([]);
  const [savePresetOpen, setSavePresetOpen] = useState(false);
  const [savePresetName, setSavePresetName] = useState("");
  const [activePresetId, setActivePresetId] = useState<string | null>(null);

  function parseHistoryPages(): number {
    const n = parseInt(historyPagesInput.trim(), 10);
    if (Number.isFinite(n)) return Math.max(1, Math.min(2000, n));
    return 50;
  }

  const [colOpen, setColOpen] = useState(false);
  const [threadOpen, setThreadOpen] = useState(false);
  const [colTitle, setColTitle] = useState("");
  const [colUrl, setColUrl] = useState("");
  const [editCol, setEditCol] = useState<Column | null>(null);
  const [editColTitle, setEditColTitle] = useState("");
  const [editColUrls, setEditColUrls] = useState("");
  const [infoMsg, setInfoMsg] = useState<string | null>(null);
  const [threadCol, setThreadCol] = useState("");
  const [threadUrl, setThreadUrl] = useState("");
  const [threadInit, setThreadInit] = useState("unspecified");
  const [threadTitle, setThreadTitle] = useState("");

  const [syncingColId, setSyncingColId] = useState<string | null>(null);

  const [rulesColId, setRulesColId] = useState<string | null>(null);
  const [rulesReporter, setRulesReporter] = useState("");
  const [rulesIntro, setRulesIntro] = useState(true);
  const [rulesRequireAddressed, setRulesRequireAddressed] = useState(true);
  const [rulesSelfOnly, setRulesSelfOnly] = useState(false);
  const [rulesPruning, setRulesPruning] = useState(false);

  const [jiraMode, setJiraMode] = useState<"assigned" | "created" | "watching">("assigned");
  const [jiraCols, setJiraCols] = useState<JiraColumnDef[]>(() => loadJiraColumns(null));
  const [jiraColFiltersMap, setJiraColFiltersMap] = useState<Map<string, JiraColFilters>>(() => new Map());
  const [jiraColOpen, setJiraColOpen] = useState(false);
  const [newJiraColTitle, setNewJiraColTitle] = useState("");
  const [newJiraColKeys, setNewJiraColKeys] = useState("");
  const [editJiraCol, setEditJiraCol] = useState<JiraColumnDef | null>(null);
  const [editJiraColTitle, setEditJiraColTitle] = useState("");
  const [editJiraColKeys, setEditJiraColKeys] = useState("");
  const [jiraConfigureUrl, setJiraConfigureUrl] = useState("");
  const [jiraConfigureToken, setJiraConfigureToken] = useState("");
  const [jiraSavingCreds, setJiraSavingCreds] = useState(false);
  const [jiraDragId, setJiraDragId] = useState<string | null>(null);
  const [jiraLoadedCount, setJiraLoadedCount] = useState(0);
  const [jiraColWidthPx, setJiraColWidthPx] = useState(() => loadJiraColWidthPx());
  const [jiraBoardPrefs, setJiraBoardPrefs] = useState<JiraBoardPrefs>(() => loadJiraBoardPrefs(null));
  const [timeColWidthPx, setTimeColWidthPx] = useState(() => loadTimeColWidthPx());
  const [timeDragId, setTimeDragId] = useState<string | null>(null);
  const [timeDragList, setTimeDragList] = useState<Column[] | null>(null);
  const timeDragInitialIdsRef = useRef<string[]>([]);

  const [columnFilters, setColumnFilters] = useState<Map<string, ColumnFilters>>(new Map());
  const [localAccounts, setLocalAccounts] = useState<LocalAccountRow[]>([]);
  const [accountSwitching, setAccountSwitching] = useState(false);

  // Jira action modal
  const [jiraActionIssue, setJiraActionIssue] = useState<JiraIssueRow | null>(null);
  const [jiraActionType, setJiraActionType] = useState<"comment" | "assign" | "transition" | null>(null);
  const [jiraActionComment, setJiraActionComment] = useState("");
  const [jiraActionFiles, setJiraActionFiles] = useState<File[]>([]);
  const [jiraActionTransitions, setJiraActionTransitions] = useState<JiraTransition[]>([]);
  const [jiraActionAssigneeQuery, setJiraActionAssigneeQuery] = useState("");
  const [jiraActionAssigneeName, setJiraActionAssigneeName] = useState("");
  const [jiraUserResults, setJiraUserResults] = useState<JiraUser[]>([]);
  const [jiraActionSubmitting, setJiraActionSubmitting] = useState(false);
  const [jiraActionError, setJiraActionError] = useState<string | null>(null);

  // Notification settings
  const [notifOpen, setNotifOpen] = useState(false);
  const [notifSettings, setNotifSettings] = useState<NotifSettings | null>(null);
  const [notifSaving, setNotifSaving] = useState(false);
  const [notifChannelUrl, setNotifChannelUrl] = useState("");
  const [notifEnabled, setNotifEnabled] = useState(false);
  const [notifComments, setNotifComments] = useState(true);
  const [notifInterval, setNotifInterval] = useState(180);
  const [notifTestMsg, setNotifTestMsg] = useState<string | null>(null);
  const [jiraWatchers, setJiraWatchers] = useState<JiraWatcherRow[]>([]);
  const [newWatcherLabel, setNewWatcherLabel] = useState("");
  const [newWatcherJql, setNewWatcherJql] = useState("");
  const [newWatcherType, setNewWatcherType] = useState("custom");
  const [notifError, setNotifError] = useState<string | null>(null);

  const loadAll = useCallback(async (overrides?: {siteId?: string; q?: string; initiator?: string; includeResolved?: boolean; authUsername?: string | null}) => {
    setErr(null);
    try {
      const resolvedSiteId = overrides?.siteId !== undefined ? overrides.siteId : siteId;
      const resolvedQ = overrides?.q !== undefined ? overrides.q : q;
      const resolvedInitiator = overrides?.initiator !== undefined ? overrides.initiator : initiator;
      const resolvedIncludeResolved = overrides?.includeResolved !== undefined ? overrides.includeResolved : includeResolved;
      const caseQs = new URLSearchParams();
      caseQs.set("include_resolved", resolvedIncludeResolved ? "true" : "false");
      if (resolvedInitiator) caseQs.set("initiator", resolvedInitiator);
      if (resolvedSiteId.trim()) caseQs.set("site_id", resolvedSiteId.trim());
      if (resolvedQ.trim()) caseQs.set("q", resolvedQ.trim());
      const a = await api<AuthStatus>("/api/auth/status");
      setAuth(a);
      if (!a.account_id) {
        setColumns([]);
        setCases([]);
        return;
      }
      const [cols, cs0] = await Promise.all([
        api<Column[]>("/api/columns"),
        api<CaseRow[]>(`/api/cases?${caseQs.toString()}`),
      ]);
      setColumns(cols);
      setCases(cs0);
    } catch (e) {
      setErr(String(e));
    }
  }, [siteId, q, initiator, includeResolved]);

  const authAccountIdRef = useRef<string | null>(null);
  useEffect(() => {
    authAccountIdRef.current = auth?.account_id ?? null;
  }, [auth?.account_id]);

  useEffect(() => {
    if (!auth) return;
    const aid = auth.account_id ?? null;
    setPresets(loadPresetsForAccount(aid));
    setColumnFilters(loadColumnFiltersFromStorage(aid));
    const v2 = localStorage.getItem(presetsStorageKey(aid));
    if ((!v2 || v2 === "[]") && aid) {
      const legacy = localStorage.getItem(LEGACY_PRESETS_KEY);
      if (legacy && legacy !== "[]") {
        localStorage.setItem(presetsStorageKey(aid), legacy);
        try {
          setPresets(JSON.parse(legacy) as FilterPreset[]);
        } catch {
          /* ignore */
        }
      }
    }
  }, [auth?.account_id, auth]);

  useEffect(() => {
    void loadAll();
  }, [includeResolved, initiator]);

  async function refreshLocalAccounts() {
    try {
      const d = await api<{ accounts: LocalAccountRow[] }>("/api/auth/accounts");
      setLocalAccounts(d.accounts);
    } catch {
      setLocalAccounts([]);
    }
  }

  useEffect(() => {
    if (mainTab !== "time" || !auth?.oauth_configured) return;
    void refreshLocalAccounts();
  }, [mainTab, auth?.oauth_configured, auth?.logged_in, auth?.account_id]);

  async function switchBoardAccount(accountId: string) {
    if (!accountId || auth?.multi_account_switch_disabled) return;
    setAccountSwitching(true);
    setErr(null);
    try {
      await api("/api/auth/active", {
        method: "POST",
        body: JSON.stringify({ account_id: accountId }),
      });
      await loadAll();
      await refreshLocalAccounts();
    } catch (e) {
      setErr(String(e));
    } finally {
      setAccountSwitching(false);
    }
  }

  async function disconnectOAuth() {
    if (!confirm("Сбросить OAuth-токен для текущей доски? Колонки останутся, понадобится снова «Войти через Time».")) return;
    setErr(null);
    try {
      await api("/api/auth/disconnect", { method: "POST" });
      await loadAll();
      await refreshLocalAccounts();
    } catch (e) {
      setErr(String(e));
    }
  }

  async function insertMyReporterFromTimeProfile() {
    setErr(null);
    try {
      const me = await api<Record<string, string | undefined>>("/api/time/profile");
      const parts: string[] = [];
      const un = (me.username || "").trim();
      if (un) parts.push(`@${un}`);
      const fn = (me.first_name || me.FirstName || "").trim();
      const ln = (me.last_name || me.LastName || "").trim();
      if (fn || ln) parts.push(`${fn} ${ln}`.trim());
      const nick = (me.nickname || "").trim();
      if (nick) parts.push(nick);
      const existing = rulesReporter
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const merged = [...new Set([...existing, ...parts])].join(", ");
      setRulesReporter(merged);
    } catch (e) {
      setErr(String(e));
    }
  }

  useEffect(() => {
    if (!infoMsg) return;
    const t = setTimeout(() => setInfoMsg(null), 10000);
    return () => clearTimeout(t);
  }, [infoMsg]);

  useEffect(() => {
    void api<JiraStatus>("/api/jira/status")
      .then(setJiraStatus)
      .catch(() =>
        setJiraStatus({
          configured: false,
          jira_host: null,
          user_hint: null,
          jira_account_id: null,
          jira_display_name: null,
          jira_name: null,
        }),
      );
  }, []);

  useEffect(() => {
    const aid = auth?.account_id ?? null;
    setJiraCols(loadJiraColumns(aid));
    setJiraColFiltersMap(loadJiraColFilters(aid));
    setJiraBoardPrefs(loadJiraBoardPrefs(aid));
  }, [auth?.account_id]);

  async function loadJiraIssues(mode?: typeof jiraMode) {
    setJiraLoading(true);
    setErr(null);
    setJiraPingOk(null);
    try {
      try {
        const st = await api<JiraStatus>("/api/jira/status");
        setJiraStatus(st);
      } catch {
        /* оставляем предыдущий status */
      }
      const effectiveMode = mode ?? jiraMode;
      const cols = jiraCols.length > 0 ? jiraCols : DEFAULT_JIRA_COLUMNS;
      const jql = buildBoardJql(effectiveMode, cols);
      const qs = new URLSearchParams({
        jql,
        max_results: "100",
        max_fetch: "1200",
      });
      const d = await api<JiraIssuesResponse>(`/api/jira/issues?${qs}`);
      setJiraIssues(d.issues);
      setJiraTotal(d.total);
      setJiraLoadedCount(d.loaded_count ?? d.issues.length);
      setJiraJql(d.jql_used || "");
    } catch (e) {
      setErr(String(e));
      setJiraIssues([]);
      setJiraTotal(0);
      setJiraLoadedCount(0);
    } finally {
      setJiraLoading(false);
    }
  }

  async function saveJiraConfigure() {
    const u = jiraConfigureUrl.trim();
    const t = jiraConfigureToken.trim();
    if (!u || !t) {
      setErr("Укажите URL Jira и токен");
      return;
    }
    setJiraSavingCreds(true);
    setErr(null);
    try {
      await api<{ ok: boolean; jira_host?: string }>("/api/jira/configure", {
        method: "POST",
        body: JSON.stringify({ jira_base_url: u, jira_token: t }),
      });
      setJiraConfigureToken("");
      const st = await api<JiraStatus>("/api/jira/status");
      setJiraStatus(st);
      setInfoMsg("Jira: настройки записаны в локальный .env и применены.");
      if (st.configured) void loadJiraIssues();
    } catch (e) {
      setErr(String(e));
    } finally {
      setJiraSavingCreds(false);
    }
  }

  function getJiraColFilters(colId: string): JiraColFilters {
    return jiraColFiltersMap.get(colId) || { siteId: "", q: "" };
  }

  function setJiraColFilter(colId: string, updates: Partial<JiraColFilters>) {
    setJiraColFiltersMap((prev) => {
      const cur = prev.get(colId) || { siteId: "", q: "" };
      const next = new Map(prev).set(colId, { ...cur, ...updates });
      saveJiraColFilters(authAccountIdRef.current, next);
      return next;
    });
  }

  function persistJiraCols(next: JiraColumnDef[]) {
    setJiraCols(next);
    saveJiraColumns(auth?.account_id, next);
  }

  function addJiraColumn() {
    const keys = newJiraColKeys
      .split(/[,\s]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    const maxPos = jiraCols.length ? Math.max(...jiraCols.map((c) => c.position)) : 0;
    const col: JiraColumnDef = {
      id: `jc_${Date.now()}`,
      title: newJiraColTitle.trim() || "Колонка",
      projectKeys: keys,
      position: maxPos + 1,
    };
    persistJiraCols([...jiraCols, col]);
    setNewJiraColTitle("");
    setNewJiraColKeys("");
    setJiraColOpen(false);
  }

  function removeJiraColumn(id: string) {
    if (!confirm("Удалить колонку Jira?")) return;
    let next = jiraCols.filter((c) => c.id !== id).map((c, i) => ({ ...c, position: i }));
    if (next.length === 0) next = [...DEFAULT_JIRA_COLUMNS];
    persistJiraCols(next);
  }

  function saveEditJiraColumn() {
    if (!editJiraCol) return;
    const keys = editJiraColKeys
      .split(/[,\s]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    const next = jiraCols.map((c) =>
      c.id === editJiraCol.id ? { ...c, title: editJiraColTitle.trim() || c.title, projectKeys: keys } : c
    );
    persistJiraCols(next);
    setEditJiraCol(null);
  }

  function moveJiraColumn(colId: string, dir: -1 | 1) {
    const sorted = [...jiraCols].sort((a, b) => a.position - b.position);
    const i = sorted.findIndex((c) => c.id === colId);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= sorted.length) return;
    const copy = [...sorted];
    const tmp = copy[i];
    copy[i] = copy[j];
    copy[j] = tmp;
    persistJiraCols(copy.map((c, idx) => ({ ...c, position: idx })));
  }

  function onJiraColumnDragStart(colId: string) {
    setJiraDragId(colId);
  }

  function onJiraColumnDragOver(e: DragEvent, overColId: string) {
    e.preventDefault();
    if (!jiraDragId || jiraDragId === overColId) return;
    const sorted = [...jiraCols].sort((a, b) => a.position - b.position);
    const from = sorted.findIndex((c) => c.id === jiraDragId);
    const to = sorted.findIndex((c) => c.id === overColId);
    if (from < 0 || to < 0) return;
    const copy = [...sorted];
    const [removed] = copy.splice(from, 1);
    copy.splice(to, 0, removed);
    persistJiraCols(copy.map((c, idx) => ({ ...c, position: idx })));
    setJiraDragId(overColId);
  }

  function onJiraColumnDragEnd() {
    setJiraDragId(null);
  }

  const timeColsSorted = useMemo(
    () => [...columns].sort((a, b) => a.position - b.position),
    [columns]
  );

  async function persistTimeColumnPositions(ordered: Column[]) {
    await Promise.all(
      ordered.map((c, idx) =>
        api(`/api/columns/${c.id}`, { method: "PATCH", body: JSON.stringify({ position: idx }) })
      )
    );
    await loadAll();
  }

  function moveTimeColumn(colId: string, dir: -1 | 1) {
    const sorted = [...columns].sort((a, b) => a.position - b.position);
    const i = sorted.findIndex((c) => c.id === colId);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= sorted.length) return;
    const copy = [...sorted];
    const tmp = copy[i];
    copy[i] = copy[j];
    copy[j] = tmp;
    void persistTimeColumnPositions(copy);
  }

  async function deleteTimeColumn(colId: string) {
    if (!confirm("Удалить колонку? Кейсы этой колонки в БД будут удалены вместе с ней.")) return;
    setErr(null);
    try {
      await api(`/api/columns/${colId}`, { method: "DELETE" });
      setColumnFilters((prev) => {
        const next = new Map(prev);
        next.delete(colId);
        saveColumnFiltersToStorage(auth?.active_account_id ?? auth?.account_id, next);
        return next;
      });
      await loadAll();
      setInfoMsg("Колонка удалена.");
    } catch (e) {
      setErr(String(e));
    }
  }

  function onTimeColumnDragStart(colId: string) {
    setTimeDragId(colId);
    const sorted = [...columns].sort((a, b) => a.position - b.position);
    timeDragInitialIdsRef.current = sorted.map((c) => c.id);
    setTimeDragList(sorted);
  }

  function onTimeColumnDragOver(e: DragEvent, overColId: string) {
    e.preventDefault();
    if (!timeDragId || timeDragId === overColId) return;
    setTimeDragList((prev) => {
      const list = prev ?? [...columns].sort((a, b) => a.position - b.position);
      const from = list.findIndex((c) => c.id === timeDragId);
      const to = list.findIndex((c) => c.id === overColId);
      if (from < 0 || to < 0) return prev;
      const copy = [...list];
      const [removed] = copy.splice(from, 1);
      copy.splice(to, 0, removed);
      return copy;
    });
    setTimeDragId(overColId);
  }

  async function onTimeColumnDragEnd() {
    const initial = timeDragInitialIdsRef.current;
    const list = timeDragList;
    setTimeDragId(null);
    setTimeDragList(null);
    if (!list || list.length === 0) return;
    const newIds = list.map((c) => c.id);
    const same =
      newIds.length === initial.length && newIds.every((id, i) => id === initial[i]);
    if (same) return;
    try {
      await Promise.all(
        list.map((c, idx) =>
          api(`/api/columns/${c.id}`, { method: "PATCH", body: JSON.stringify({ position: idx }) })
        )
      );
      await loadAll();
    } catch (e) {
      setErr(String(e));
      await loadAll();
    }
  }

  function switchJiraMode(m: typeof jiraMode) {
    setJiraMode(m);
    if (jiraStatus?.configured) void loadJiraIssues(m);
  }

  async function jiraPing() {
    setErr(null);
    setJiraPingOk(null);
    try {
      const r = await api<{ ok: boolean; name?: string; display_name?: string }>("/api/jira/ping");
      setJiraPingOk(r.display_name || r.name || "ok");
      try {
        const st = await api<JiraStatus>("/api/jira/status");
        setJiraStatus(st);
      } catch {
        /* ignore */
      }
    } catch (e) {
      setErr(String(e));
    }
  }

  useEffect(() => {
    if (mainTab === "jira" && jiraStatus?.configured) void loadJiraIssues();
  }, [mainTab, jiraStatus?.configured]);

  // ── Jira action modal ───────────────────────────────────────────────────────

  async function openJiraAction(issue: JiraIssueRow, type: "comment" | "assign" | "transition") {
    setJiraActionIssue(issue);
    setJiraActionType(type);
    setJiraActionComment("");
    setJiraActionFiles([]);
    setJiraActionAssigneeQuery("");
    setJiraActionAssigneeName("");
    setJiraUserResults([]);
    setJiraActionError(null);
    setJiraActionTransitions([]);
    if (type === "transition") {
      try {
        const r = await api<{ transitions: JiraTransition[] }>(`/api/jira/issues/${issue.key}/transitions`);
        setJiraActionTransitions(r.transitions || []);
      } catch (e) {
        setJiraActionError(String(e));
      }
    }
  }

  function closeJiraAction() {
    setJiraActionIssue(null);
    setJiraActionType(null);
    setJiraActionError(null);
  }

  async function submitJiraComment() {
    if (!jiraActionIssue) return;
    setJiraActionSubmitting(true);
    setJiraActionError(null);
    try {
      if (jiraActionFiles.length > 0) {
        const form = new FormData();
        form.append("comment", jiraActionComment);
        for (const f of jiraActionFiles) form.append("files", f);
        const r = await fetch(`/api/jira/issues/${jiraActionIssue.key}/comment-with-images`, { method: "POST", body: form });
        if (!r.ok) throw new Error(await r.text());
      } else {
        await api(`/api/jira/issues/${jiraActionIssue.key}/comment`, {
          method: "POST",
          body: JSON.stringify({ text: jiraActionComment }),
        });
      }
      setInfoMsg(`Комментарий добавлен к ${jiraActionIssue.key}`);
      closeJiraAction();
      void loadJiraIssues();
    } catch (e) {
      setJiraActionError(String(e));
    } finally {
      setJiraActionSubmitting(false);
    }
  }

  async function submitJiraTransition(transitionId: string, transitionName: string) {
    if (!jiraActionIssue) return;
    setJiraActionSubmitting(true);
    setJiraActionError(null);
    try {
      await api(`/api/jira/issues/${jiraActionIssue.key}/transition`, {
        method: "POST",
        body: JSON.stringify({ transition_id: transitionId }),
      });
      setInfoMsg(`${jiraActionIssue.key} → ${transitionName}`);
      closeJiraAction();
      void loadJiraIssues();
    } catch (e) {
      setJiraActionError(String(e));
    } finally {
      setJiraActionSubmitting(false);
    }
  }

  async function submitJiraAssign() {
    if (!jiraActionIssue) return;
    setJiraActionSubmitting(true);
    setJiraActionError(null);
    try {
      await api(`/api/jira/issues/${jiraActionIssue.key}/assign`, {
        method: "POST",
        body: JSON.stringify({ assignee_name: jiraActionAssigneeName || null }),
      });
      setInfoMsg(`${jiraActionIssue.key} назначен: ${jiraActionAssigneeName || "снят"}`);
      closeJiraAction();
      void loadJiraIssues();
    } catch (e) {
      setJiraActionError(String(e));
    } finally {
      setJiraActionSubmitting(false);
    }
  }

  const jiraUserSearchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  function searchJiraUsers(q: string) {
    setJiraActionAssigneeQuery(q);
    if (jiraUserSearchTimer.current) clearTimeout(jiraUserSearchTimer.current);
    if (!q.trim()) { setJiraUserResults([]); return; }
    jiraUserSearchTimer.current = setTimeout(async () => {
      try {
        const r = await api<{ users: JiraUser[] }>(`/api/jira/users?q=${encodeURIComponent(q)}`);
        setJiraUserResults(r.users || []);
      } catch { /* ignore */ }
    }, 350);
  }

  // ── Notification settings ───────────────────────────────────────────────────

  async function openNotifSettings() {
    setNotifOpen(true);
    setNotifError(null);
    setNotifTestMsg(null);
    try {
      const [ns, ww] = await Promise.all([
        api<NotifSettings>("/api/notifications/settings"),
        api<JiraWatcherRow[]>("/api/jira/watchers"),
      ]);
      setNotifSettings(ns);
      setNotifChannelUrl(ns.time_channel_url || "");
      setNotifEnabled(ns.enabled);
      setNotifComments(ns.notify_new_comments);
      setNotifInterval(ns.poll_interval_sec || 180);
      setJiraWatchers(ww);
    } catch (e) {
      setNotifError(String(e));
    }
  }

  async function saveNotifSettings() {
    setNotifSaving(true);
    setNotifError(null);
    try {
      const ns = await api<NotifSettings>("/api/notifications/settings", {
        method: "POST",
        body: JSON.stringify({
          time_channel_url: notifChannelUrl,
          enabled: notifEnabled,
          notify_new_comments: notifComments,
          poll_interval_sec: notifInterval,
        }),
      });
      setNotifSettings(ns);
      setNotifChannelUrl(ns.time_channel_url || "");
      setNotifTestMsg("Сохранено");
    } catch (e) {
      setNotifError(String(e));
    } finally {
      setNotifSaving(false);
    }
  }

  async function testNotification() {
    setNotifTestMsg(null);
    setNotifError(null);
    try {
      await api("/api/notifications/test", { method: "POST" });
      setNotifTestMsg("Тест отправлен в Time!");
    } catch (e) {
      setNotifError(String(e));
    }
  }

  async function addJiraWatcher() {
    if (!newWatcherJql.trim()) return;
    try {
      const w = await api<JiraWatcherRow>("/api/jira/watchers", {
        method: "POST",
        body: JSON.stringify({ label: newWatcherLabel, jql: newWatcherJql, watcher_type: newWatcherType }),
      });
      setJiraWatchers((prev) => [...prev, w]);
      setNewWatcherLabel("");
      setNewWatcherJql("");
    } catch (e) {
      setNotifError(String(e));
    }
  }

  async function deleteJiraWatcher(id: string) {
    try {
      await api(`/api/jira/watchers/${id}`, { method: "DELETE" });
      setJiraWatchers((prev) => prev.filter((w) => w.id !== id));
    } catch (e) {
      setNotifError(String(e));
    }
  }

  async function toggleJiraWatcher(id: string, enabled: boolean) {
    try {
      const w = await api<JiraWatcherRow>(`/api/jira/watchers/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      setJiraWatchers((prev) => prev.map((x) => (x.id === id ? w : x)));
    } catch (e) {
      setNotifError(String(e));
    }
  }

  const loadRef = useRef(loadAll);
  loadRef.current = loadAll;
  const authRef = useRef(auth);
  authRef.current = auth;

  useEffect(() => {
    const id = setInterval(() => {
      if (authRef.current?.logged_in) void loadRef.current();
    }, 45000);
    return () => clearInterval(id);
  }, []);

  const jiraColsSorted = useMemo(
    () => [...jiraCols].sort((a, b) => a.position - b.position),
    [jiraCols]
  );

  const jiraIssuesForBoard = useMemo(() => {
    if (!jiraBoardPrefs.hideLastCommentMine || !jiraStatusHasIdentity(jiraStatus)) return jiraIssues;
    return jiraIssues.filter((row) => !jiraLastCommentIsMine(row, jiraStatus));
  }, [jiraIssues, jiraBoardPrefs.hideLastCommentMine, jiraStatus]);

  const jiraByColumn = useMemo(() => {
    const m = new Map<string, JiraIssueRow[]>();
    const cols = jiraColsSorted.length > 0 ? jiraColsSorted : DEFAULT_JIRA_COLUMNS;
    for (const col of cols) m.set(col.id, []);
    for (const issue of jiraIssuesForBoard) {
      const cid = assignIssueToColumnId(issue, cols);
      if (!cid) continue;
      m.get(cid)?.push(issue);
    }
    for (const arr of m.values()) {
      arr.sort((a, b) =>
        jiraCompareIssues(a, b, jiraStatus, jiraBoardPrefs.prioritizeOthersLastComment),
      );
    }
    return m;
  }, [
    jiraIssuesForBoard,
    jiraColsSorted,
    jiraStatus,
    jiraBoardPrefs.prioritizeOthersLastComment,
  ]);

  const byColumn = useMemo(() => {
    const m = new Map<string, CaseRow[]>();
    for (const c of columns) m.set(c.id, []);
    for (const c of cases) {
      const arr = m.get(c.column_id);
      if (arr) arr.push(c);
    }
    for (const arr of m.values()) {
      arr.sort((a, b) => b.last_activity_at_ms - a.last_activity_at_ms);
    }
    return m;
  }, [columns, cases]);

  const activeCount = (cid: string) => (byColumn.get(cid) || []).filter((c) => c.status === "active").length;

  function getColumnFilters(colId: string): ColumnFilters {
    return columnFilters.get(colId) || {
      onlyMyCases: false,
      includeResolved: true,
      siteId: "",
      q: "",
    };
  }

  function setColumnFilter(colId: string, updates: Partial<ColumnFilters>) {
    setColumnFilters((prev) => {
      const current = prev.get(colId) || {
        onlyMyCases: false,
        includeResolved: true,
        siteId: "",
        q: "",
      };
    const updated = { ...current, ...updates };
      const next = new Map(prev).set(colId, updated);
      saveColumnFiltersToStorage(authAccountIdRef.current, next);
      return next;
    });
  }

  function getFilteredCasesForColumn(colId: string): CaseRow[] {
    const all = byColumn.get(colId) || [];
    const filters = getColumnFilters(colId);
    let filtered = [...all];

    if (!filters.includeResolved) {
      filtered = filtered.filter((c) => c.status === "active");
    }

    if (filters.siteId.trim()) {
      const siteIds = filters.siteId.split(",").map((s) => s.trim()).filter(Boolean);
      filtered = filtered.filter((c) => c.site_id && siteIds.includes(c.site_id));
    }

    if (filters.q.trim()) {
      const ql = filters.q.toLowerCase();
      filtered = filtered.filter(
        (c) =>
          (c.thread_search_text || "").toLowerCase().includes(ql) ||
          (c.title_preview || "").toLowerCase().includes(ql) ||
          (c.last_message_preview || "").toLowerCase().includes(ql)
      );
    }

    if (filters.onlyMyCases && auth?.account_id) {
      const me = auth.account_id;
      filtered = filtered.filter((c) => {
        if (c.root_author_user_id && c.root_author_user_id === me) return true;
        if (!c.root_author_user_id && c.initiator === "self") return true;
        return false;
      });
    }

    return filtered;
  }

  async function doSync() {
    setSyncing(true);
    setErr(null);
    try {
      const d = await api<{ new_cases: number; errors?: string[] }>("/api/sync", { method: "POST" });
      await loadAll();
      if (d.errors?.length) {
        setInfoMsg(null);
        setErr(d.errors.join("; "));
      } else {
        setInfoMsg(`Общий синк: новых карточек ${d.new_cases}.`);
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setSyncing(false);
    }
  }

  async function doColumnSync(colId: string) {
    setSyncingColId(colId);
    setErr(null);
    try {
      const d = await api<{ new_cases: number; errors?: string[] }>(`/api/columns/${colId}/sync`, { method: "POST" });
      await loadAll();
      if (d.errors?.length) {
        setInfoMsg(null);
        setErr(d.errors.join("; "));
      } else {
        setInfoMsg(`Синк колонки: новых карточек ${d.new_cases}. Если 0 — нажмите «История» (можно «с нуля»).`);
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setSyncingColId(null);
    }
  }

  async function doHistorySync() {
    const pages = parseHistoryPages();
    setHistoryPagesInput(String(pages));
    setHistorySyncing(true);
    setErr(null);
    try {
      const body = JSON.stringify({ pages, reset_cursor: historyReset });
      let d: { new_cases: number; errors: string[] };
      try {
        d = await api("/api/history/pull", { method: "POST", body });
      } catch {
        d = await api("/api/sync/history", { method: "POST", body });
      }
      await loadAll();
      if (d.errors?.length) {
        setInfoMsg(null);
        setErr(d.errors.join("; "));
      } else {
        setInfoMsg(`История: новых карточек ${d.new_cases}.`);
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setHistorySyncing(false);
    }
  }

  async function addColumn() {
    setErr(null);
    const urls = colUrl
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!colTitle.trim()) {
      setErr("Укажите название колонки");
      return;
    }
    if (urls.length === 0) {
      setErr("Укажите хотя бы один URL канала (можно несколько — по одному на строку)");
      return;
    }
    try {
      // Всегда шлём channel_url (первый URL) — старые инстансы uvicorn требовали это поле.
      // Несколько каналов — дополнительно channel_urls; бэкенд берёт список в приоритете.
      await api("/api/columns", {
        method: "POST",
        body: JSON.stringify({
          title: colTitle.trim(),
          channel_url: urls[0],
          ...(urls.length > 1 ? { channel_urls: urls } : {}),
        }),
      });
      setColOpen(false);
      setColTitle("");
      setColUrl("");
      await loadAll();
    } catch (e) {
      setErr(String(e));
    }
  }

  function openEditColumn(col: Column) {
    setErr(null);
    setEditCol(col);
    setEditColTitle(col.title);
    const urlStr = columnUrlsForEdit(col);
    // Если в колонке только один URL и это fallback-значение (channel_name без channels_json),
    // показываем его с припиской чтобы пользователь добавил остальные
    setEditColUrls(urlStr);
  }

  async function doHistorySyncColumn(colId: string) {
    setSyncingColId(colId);
    setErr(null);
    const pages = parseHistoryPages();
    try {
      const d = await api<{ new_cases: number; errors?: string[] }>(
        `/api/columns/${colId}/history-reset?pages=${encodeURIComponent(String(pages))}`,
        { method: "POST" },
      );
      await loadAll();
      if (d.errors?.length) {
        setInfoMsg(null);
        setErr(d.errors.join("; "));
      } else {
        setInfoMsg(
          `Загрузка истории (${pages} стр.): новых карточек ${d.new_cases}. Если мало — увеличьте число страниц в панели и снова «Загрузить историю».`
        );
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setSyncingColId(null);
    }
  }

  async function saveEditColumn() {
    if (!editCol) return;
    const urls = editColUrls
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!editColTitle.trim()) {
      setErr("Укажите название колонки");
      return;
    }
    if (urls.length === 0) {
      setErr("Укажите хотя бы один URL канала");
      return;
    }
    setErr(null);
    try {
      await api(`/api/columns/${editCol.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title: editColTitle.trim(), channel_urls: urls }),
      });
      setEditCol(null);
      await loadAll();
      setInfoMsg("Колонка сохранена. Нажмите ↻ у колонки (или «История» для старых постов).");
    } catch (e) {
      setErr(String(e));
    }
  }

  function openThreadModal() {
    // default to first column if none selected yet
    setThreadCol(prev => prev || (columns.length > 0 ? columns[0].id : ""));
    setThreadUrl("");
    setThreadTitle("");
    setThreadInit("unspecified");
    setThreadOpen(true);
  }

  async function addThread() {
    const colId = threadCol || (columns.length > 0 ? columns[0].id : "");
    if (!colId) { setErr("Выберите колонку"); return; }
    setErr(null);
    try {
      await api("/api/cases/manual", {
        method: "POST",
        body: JSON.stringify({
          column_id: colId,
          permalink: threadUrl,
          initiator: threadInit,
          custom_title: threadTitle.trim() || undefined,
        }),
      });
      setThreadOpen(false);
      setThreadUrl("");
      setThreadTitle("");
      await loadAll();
    } catch (e) {
      setErr(String(e));
    }
  }

  async function resolveCase(id: string) {
    await api(`/api/cases/${id}/resolve`, { method: "POST" });
    await loadAll();
  }

  async function ignoreCase(id: string) {
    if (!confirm("Убрать навсегда из авто-синка?")) return;
    await api(`/api/cases/${id}/ignore_forever`, { method: "POST" });
    await loadAll();
  }

  async function patchInitiator(id: string, v: string) {
    await api(`/api/cases/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ initiator: v }),
    });
    await loadAll();
  }

  function openRulesModal(col: Column) {
    setRulesColId(col.id);
    try {
      const r = JSON.parse(col.rules_json || "{}");
      const arr = r.reporter_substrings;
      setRulesReporter(Array.isArray(arr) ? arr.join(", ") : "");
      setRulesIntro(!!r.support_intro_required);
      const ra = r.require_addressed_to_me;
      setRulesRequireAddressed(typeof ra === "boolean" ? ra : true);
      setRulesSelfOnly(!!r.match_self_only);
    } catch {
      setRulesReporter("");
      setRulesIntro(true);
      setRulesRequireAddressed(true);
      setRulesSelfOnly(false);
    }
  }

  function applyPreset(p: FilterPreset) {
    setActivePresetId(p.id);
    setSiteId(p.siteId);
    if (p.q !== undefined) setQ(p.q);
    if (p.initiator !== undefined) setInitiator(p.initiator);
    void loadAll({ siteId: p.siteId, q: p.q ?? q, initiator: p.initiator ?? initiator });
  }

  function deletePreset(id: string) {
    const updated = presets.filter(p => p.id !== id);
    setPresets(updated);
    savePresetsForAccount(auth?.account_id ?? null, updated);
    if (activePresetId === id) setActivePresetId(null);
  }

  function commitSavePreset() {
    const name = savePresetName.trim() || siteId || q || "Фильтр";
    const p: FilterPreset = {
      id: crypto.randomUUID(),
      name,
      siteId,
      q: q || undefined,
      initiator: initiator || undefined,
    };
    const updated = [...presets, p];
    setPresets(updated);
    savePresetsForAccount(auth?.account_id ?? null, updated);
    setActivePresetId(p.id);
    setSavePresetOpen(false);
    setSavePresetName("");
  }

  function mergedRulesJson(col: Column): string {
    let r: Record<string, unknown> = {};
    try {
      r = JSON.parse(col.rules_json || "{}") as Record<string, unknown>;
    } catch {
      r = {};
    }
    r.reporter_substrings = rulesReporter
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    r.match_self_only = rulesSelfOnly;
    r.support_intro_required = rulesSelfOnly ? false : rulesIntro;
    r.require_addressed_to_me = rulesSelfOnly ? false : rulesRequireAddressed;
    r.match_mentions_me = rulesSelfOnly ? false : rulesRequireAddressed;
    return JSON.stringify(r);
  }

  async function saveColumnRules() {
    if (!rulesColId) return;
    const col = columns.find((c) => c.id === rulesColId);
    if (!col) return;
    setErr(null);
    try {
      await api(`/api/columns/${rulesColId}`, {
        method: "PATCH",
        body: JSON.stringify({ rules_json: mergedRulesJson(col) }),
      });
      setRulesColId(null);
      await loadAll();
    } catch (e) {
      setErr(String(e));
    }
  }

  async function pruneColumn() {
    if (!rulesColId) return;
    if (!confirm("Удалить с доски карточки, которые не проходят текущие правила?")) return;
    const col = columns.find((c) => c.id === rulesColId);
    if (!col) return;
    setRulesPruning(true);
    setErr(null);
    try {
      await api(`/api/columns/${rulesColId}`, {
        method: "PATCH",
        body: JSON.stringify({ rules_json: mergedRulesJson(col) }),
      });
      await api(`/api/columns/${rulesColId}/prune`, { method: "POST" });
      setRulesColId(null);
      await loadAll();
    } catch (e) {
      setErr(String(e));
    } finally {
      setRulesPruning(false);
    }
  }

  return (
    <>
      <header className="toolbar">
        <h1>Case Board</h1>
        <div className="tabs">
          <button
            type="button"
            className={"tab-btn" + (mainTab === "time" ? " tab-active" : "")}
            onClick={() => setMainTab("time")}
          >
            TiMe
          </button>
          <button
            type="button"
            className={"tab-btn" + (mainTab === "jira" ? " tab-active" : "")}
            onClick={() => setMainTab("jira")}
          >
            Jira
            {jiraStatus?.configured && jiraStatus.jira_host ? (
              <span style={{ opacity: 0.7, fontSize: "0.72rem" }}> · {jiraStatus.jira_host}</span>
            ) : null}
          </button>
        </div>
        {mainTab === "time" && auth && (
          <span className="row" style={{ alignItems: "center", gap: "0.45rem", flexWrap: "wrap" }}>
          <span style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
            {auth.logged_in
              ? auth.username
                ? `@${auth.username}`
                : "авторизован"
              : "нет токена"}
              {auth.account_id ? (
                <span style={{ opacity: 0.65, fontSize: "0.72rem" }} title="id учётки TiMe для локальной БД">
                  {" "}
                  · доска: {auth.account_id.length > 12 ? `${auth.account_id.slice(0, 10)}…` : auth.account_id}
                </span>
              ) : null}
            </span>
            {auth.multi_account_switch_disabled && auth.personal_token_configured ? (
              <span
                style={{ fontSize: "0.72rem", color: "var(--muted)" }}
                title="С PAT в oauth.env активна одна учётка. Уберите TIME_PERSONAL_ACCESS_TOKEN для нескольких OAuth-логинов."
              >
                одна учётка (PAT)
              </span>
            ) : null}
            {auth.oauth_configured &&
            !auth.multi_account_switch_disabled &&
            localAccounts.filter((a) => !a.legacy).length > 0 ? (
              <label className="row" style={{ alignItems: "center", gap: "0.25rem", fontSize: "0.78rem", color: "var(--muted)" }}>
                <span>Учётка:</span>
                <select
                  value={auth.active_account_id || auth.account_id || ""}
                  onChange={(e) => void switchBoardAccount(e.target.value)}
                  disabled={accountSwitching}
                  title="Колонки и кейсы хранятся отдельно для каждой учётки на этом компьютере"
                  style={{ maxWidth: 220, fontSize: "0.78rem", padding: "0.2rem 0.35rem" }}
                >
                  {localAccounts
                    .filter((a) => !a.legacy)
                    .map((a) => (
                      <option key={`${a.file}:${a.account_id}`} value={a.account_id}>
                        {a.username ? `@${a.username}` : a.account_id}
                      </option>
                    ))}
                </select>
              </label>
            ) : null}
            {auth.oauth_configured && !auth.personal_token_configured && auth.logged_in ? (
              <button
                type="button"
                className="btn"
                style={{ fontSize: "0.72rem", padding: "0.2rem 0.45rem" }}
                title="Очистить OAuth-токен в БД этой доски (колонки не удаляются)"
                onClick={() => void disconnectOAuth()}
              >
                Сбросить OAuth
              </button>
            ) : null}
            {auth.oauth_configured && !auth.personal_token_configured && auth.logged_in ? (
              <a className="btn" href="/oauth/login" style={{ textDecoration: "none", fontSize: "0.72rem", padding: "0.2rem 0.45rem" }} title="Добавить сессию другой учётки (отдельный файл доски)">
                Другой логин Time
              </a>
            ) : null}
          </span>
        )}
        <span style={{ flex: 1 }} />
        {mainTab === "time" && (
          <>
            <input
              placeholder="Поиск по тексту"
              value={q}
              onChange={(e) => { setQ(e.target.value); setActivePresetId(null); }}
              onKeyDown={(e) => e.key === "Enter" && void loadAll()}
              style={{ minWidth: 150 }}
            />
            <input
              placeholder="site ID или 308,6321,…"
              value={siteId}
              onChange={(e) => { setSiteId(e.target.value); setActivePresetId(null); }}
              onKeyDown={(e) => e.key === "Enter" && void loadAll()}
              style={{ minWidth: 120 }}
            />
            {(siteId.trim() || q.trim()) && !savePresetOpen && (
              <button
                type="button"
                className="btn"
                title="Сохранить текущий фильтр как пресет"
                onClick={() => { setSavePresetOpen(true); setSavePresetName(siteId || q); }}
              >
                💾
              </button>
            )}
            {savePresetOpen && (
              <span className="row" style={{ alignItems: "center", gap: "0.3rem" }}>
                <input
                  autoFocus
                  placeholder="Название пресета"
                  value={savePresetName}
                  onChange={(e) => setSavePresetName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") commitSavePreset(); if (e.key === "Escape") setSavePresetOpen(false); }}
                  style={{ minWidth: 110, padding: "0.35rem 0.45rem" }}
                />
                <button type="button" className="btn btn-primary" onClick={commitSavePreset} style={{ padding: "0.35rem 0.55rem" }}>OK</button>
                <button type="button" className="btn" onClick={() => setSavePresetOpen(false)} style={{ padding: "0.35rem 0.5rem" }}>✕</button>
              </span>
            )}
            <select
              value={initiator}
              onChange={(e) => { setInitiator(e.target.value); }}
              title="Фильтр загруженных кейсов по полю «инициатор» в базе (не по автору корня в TiMe)"
            >
              <option value="">Инициатор: все</option>
              <option value="self">Я инициатор</option>
              <option value="incoming">Ко мне</option>
              <option value="unspecified">Не указан инициатор</option>
            </select>
            <label className="row" style={{ alignItems: "center", fontSize: "0.85rem", color: "var(--muted)" }}>
              <input type="checkbox" checked={includeResolved} onChange={(e) => setIncludeResolved(e.target.checked)} />
              решённые
            </label>
            <button type="button" className="btn" onClick={() => void loadAll()}>
              Применить
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={syncing}
              title="Запросить новые посты из TiMe в БД и обновить список кейсов"
              onClick={() => void doSync()}
            >
              {syncing ? "Синк…" : "Синк"}
            </button>
            <span className="row" style={{ alignItems: "center", gap: "0.3rem" }}>
              <input
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                value={historyPagesInput}
                onChange={(e) => setHistoryPagesInput(e.target.value)}
                onBlur={() => setHistoryPagesInput(String(parseHistoryPages()))}
                title="Число страниц истории (~200 постов/стр., до 2000). Для первого запуска советуем 100-500."
                className="history-pages-input"
                aria-label="Страниц истории"
              />
              <label className="row" style={{ alignItems: "center", fontSize: "0.78rem", color: "var(--muted)", gap: "0.2rem" }}>
                <input type="checkbox" checked={historyReset} onChange={(e) => setHistoryReset(e.target.checked)} />
                с нуля
              </label>
              <button
                type="button"
                className="btn"
                disabled={historySyncing || syncing}
                title="Подтянуть старые посты (пагинация TiMe). «С нуля» — полный сброс курсора."
                onClick={() => void doHistorySync()}
              >
                {historySyncing ? "История…" : "История"}
              </button>
            </span>
            <button type="button" className="btn" onClick={() => setColOpen(true)}>
              + Колонка
            </button>
            <button type="button" className="btn" onClick={openThreadModal}>
              + Тред
            </button>
            <label
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "0.35rem",
                fontSize: "0.78rem",
                color: "var(--muted)",
                marginLeft: "0.15rem",
              }}
              title="Ширина колонок TiMe (сохраняется в браузере)"
            >
              ширина
              <input
                type="range"
                min={240}
                max={720}
                step={10}
                value={timeColWidthPx}
                onChange={(e) => {
                  const n = parseInt(e.target.value, 10);
                  setTimeColWidthPx(n);
                  try {
                    localStorage.setItem(TIME_COL_WIDTH_KEY, String(n));
                  } catch {
                    /* ignore */
                  }
                }}
              />
              <span style={{ color: "var(--text)", minWidth: "2.6rem" }}>{timeColWidthPx}px</span>
            </label>
            {auth?.oauth_configured && !auth.personal_token_configured && !auth?.logged_in && (
              <a className="btn" href="/oauth/login" style={{ textDecoration: "none" }}>
                Войти через Time
              </a>
            )}
          </>
        )}
        {mainTab === "jira" && (
          <>
            <button
              type="button"
              className="btn btn-primary"
              disabled={jiraLoading || !jiraStatus?.configured}
              onClick={() => void loadJiraIssues()}
            >
              {jiraLoading ? "Загрузка…" : "Обновить"}
            </button>
            <button
              type="button"
              className="btn"
              disabled={!jiraStatus?.configured}
              onClick={() => setJiraColOpen(true)}
              title="Несколько ключей через запятую = одна колонка (мердж отделов)"
            >
              + Jira колонка
            </button>
            <button
              type="button"
              className="btn"
              disabled={!jiraStatus?.configured}
              onClick={() => void jiraPing()}
              title="GET /rest/api/2/myself — проверка токена под VPN"
            >
              Проверить Jira
            </button>
            <button
              type="button"
              className="btn"
              disabled={!jiraStatus?.configured}
              onClick={() => void openNotifSettings()}
              title="Уведомления о комментариях в Jira → Time"
            >
              Уведомления
            </button>
            {jiraPingOk ? (
              <span style={{ fontSize: "0.8rem", color: "var(--accent2)" }}>OK: {jiraPingOk}</span>
            ) : null}
            <label
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "0.35rem",
                fontSize: "0.78rem",
                color: "var(--muted)",
                marginLeft: "0.25rem",
              }}
              title="Ширина колонок на доске Jira (сохраняется в браузере)"
            >
              ширина
              <input
                type="range"
                min={280}
                max={720}
                step={10}
                value={jiraColWidthPx}
                disabled={!jiraStatus?.configured}
                onChange={(e) => {
                  const n = parseInt(e.target.value, 10);
                  setJiraColWidthPx(n);
                  try {
                    localStorage.setItem(JIRA_COL_WIDTH_KEY, String(n));
                  } catch {
                    /* ignore */
                  }
                }}
              />
              <span style={{ color: "var(--text)", minWidth: "2.6rem" }}>{jiraColWidthPx}px</span>
            </label>
          </>
        )}
      </header>
      {err && (
        <div className="error" style={{ padding: "0.5rem 1.25rem" }}>
          {err}
        </div>
      )}
      {infoMsg && (
        <div
          style={{
            padding: "0.5rem 1.25rem",
            background: "rgba(80, 160, 120, 0.12)",
            color: "var(--fg, #e8e8e8)",
            fontSize: "0.9rem",
            borderBottom: "1px solid rgba(80, 160, 120, 0.35)",
          }}
        >
          {infoMsg}
        </div>
      )}

      {mainTab === "time" && presets.length > 0 && (
        <div className="presets-bar">
          <span className="presets-label">Фильтры:</span>
          {presets.map(p => (
            <span key={p.id} className={"preset-chip" + (activePresetId === p.id ? " preset-chip-active" : "")}>
              <button
                type="button"
                className="preset-chip-btn"
                onClick={() => applyPreset(p)}
                title={`site: ${p.siteId}${p.q ? `  q: ${p.q}` : ""}`}
              >
                {p.name}
              </button>
              <button
                type="button"
                className="preset-chip-del"
                onClick={() => deletePreset(p.id)}
                aria-label="Удалить пресет"
              >✕</button>
            </span>
          ))}
          {activePresetId && (
            <button
              type="button"
              className="btn"
              style={{ fontSize: "0.72rem", padding: "0.2rem 0.5rem" }}
              onClick={() => { setActivePresetId(null); setSiteId(""); setQ(""); void loadAll({ siteId: "", q: "" }); }}
            >
              сбросить
            </button>
          )}
        </div>
      )}

      {mainTab === "jira" && (
        <div className="jira-panel jira-panel-wide">
          {!jiraStatus?.configured ? (
            <div className="jira-configure-box">
              <p style={{ color: "var(--muted)" }}>
                Jira не настроена. Можно задать <code>JIRA_BASE_URL</code> и <code>JIRA_TOKEN</code> в{" "}
                <code>oauth.env</code> / <code>.env</code> (не в git), либо один раз указать ниже — значения
                запишутся в локальный <code>.env</code> в папке приложения.
              </p>
              <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
                Токен хранится только у вас на диске; в интерфейс после сохранения не подставляется. Нужен VPN /
                доступ к хосту Jira. См. <code>SECURITY.md</code>.
              </p>
              <div className="field" style={{ maxWidth: "32rem" }}>
                <label>Базовый URL Jira</label>
                <input
                  value={jiraConfigureUrl}
                  onChange={(e) => setJiraConfigureUrl(e.target.value)}
                  placeholder="https://jira.example.com"
                  autoComplete="off"
                />
              </div>
              <div className="field" style={{ maxWidth: "32rem" }}>
                <label>Токен (Bearer / PAT)</label>
                <input
                  type="password"
                  value={jiraConfigureToken}
                  onChange={(e) => setJiraConfigureToken(e.target.value)}
                  placeholder="Вставьте токен"
                  autoComplete="off"
                />
              </div>
              <button
                type="button"
                className="btn btn-primary"
                disabled={jiraSavingCreds}
                onClick={() => void saveJiraConfigure()}
              >
                {jiraSavingCreds ? "Сохранение…" : "Сохранить в .env и подключить"}
              </button>
            </div>
          ) : (
            <>
              <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.75rem", flexWrap: "wrap" }}>
                {(["assigned", "created", "watching"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    className={"btn" + (jiraMode === m ? " btn-primary" : "")}
                    style={{ fontSize: "0.82rem" }}
                    onClick={() => switchJiraMode(m)}
                    disabled={jiraLoading}
                  >
                    {m === "assigned" ? "На мне" : m === "created" ? "Я создал" : "Я наблюдаю"}
                  </button>
                ))}
              </div>
              <div className="jira-meta">
                В Jira по JQL: <strong>{jiraTotal}</strong>
                {jiraLoadedCount !== jiraTotal ? (
                  <>
                    {" "}
                    · загружено в доску: <strong>{jiraLoadedCount}</strong>
                  </>
                ) : null}
                {jiraLoadedCount < jiraTotal ? (
                  <span style={{ color: "var(--danger)", marginLeft: "0.35rem" }}>
                    (часть задач за пределом лимита загрузки — сузьте JQL или поднимите max_fetch в API)
                  </span>
                ) : null}
                {jiraJql ? (
                  <>
                    {" "}
                    · <code className="jira-jql-inline">{jiraJql}</code>
                  </>
                ) : null}
              </div>
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: "0.65rem 1.25rem",
                  alignItems: "center",
                  margin: "0.45rem 0 0.25rem",
                  fontSize: "0.8rem",
                  color: "var(--muted)",
                }}
              >
                <label
                  style={{ display: "inline-flex", alignItems: "center", gap: "0.35rem", cursor: "pointer" }}
                  title="Сравнение с пользователем из GET /myself по тому же токену (accountId или имя автора комментария)"
                >
                  <input
                    type="checkbox"
                    checked={jiraBoardPrefs.hideLastCommentMine}
                    disabled={!jiraStatusHasIdentity(jiraStatus)}
                    onChange={(e) => {
                      const v = e.target.checked;
                      setJiraBoardPrefs((prev) => {
                        const next = { ...prev, hideLastCommentMine: v };
                        saveJiraBoardPrefs(auth?.account_id ?? null, next);
                        return next;
                      });
                    }}
                  />
                  Скрыть, где последний комментарий мой
                </label>
                <label
                  style={{ display: "inline-flex", alignItems: "center", gap: "0.35rem", cursor: "pointer" }}
                  title="Вверху колонки — где ответили не вы; ниже — где последним писали вы. Внутри группы по времени последнего комментария или updated"
                >
                  <input
                    type="checkbox"
                    checked={jiraBoardPrefs.prioritizeOthersLastComment}
                    disabled={!jiraStatusHasIdentity(jiraStatus)}
                    onChange={(e) => {
                      const v = e.target.checked;
                      setJiraBoardPrefs((prev) => {
                        const next = { ...prev, prioritizeOthersLastComment: v };
                        saveJiraBoardPrefs(auth?.account_id ?? null, next);
                        return next;
                      });
                    }}
                  />
                  Сначала «ждут моего ответа» (последний коммент не мой)
                </label>
                {!jiraStatusHasIdentity(jiraStatus) && jiraStatus?.configured ? (
                  <span style={{ fontSize: "0.76rem", color: "var(--danger)" }}>
                    Не удалось сопоставить вашу учётку Jira — нажмите «Обновить» или «Проверить Jira».
                  </span>
                ) : null}
              </div>
              <p style={{ fontSize: "0.78rem", color: "var(--muted)", marginTop: 0, maxWidth: "56rem" }}>
                Ключ проекта — буквы до дефиса в задаче (<code>AIMP-123</code> → <code>AIMP</code>). В колонке можно
                несколько ключей через запятую. <strong>Префикс:</strong> любые буквы и звёздочка в конце — например{" "}
                <code>AAR*</code>, <code>X*</code>, <code>FOO*</code> — попадут все проекты, чей ключ{" "}
                <strong>начинается с этих букв</strong> (длиннее префикс важнее короткого в одной колонке). Ключ{" "}
                <strong>без *</strong> (например <code>AAR</code>) совпадает и с проектом <code>AAR</code>, и с ключами
                длиннее (<code>AARP</code>, …). Чтобы отделить <code>AARP</code> от «общей» <code>AAR</code>, добавьте
                колонку <code>AARP</code> и поставьте её <strong>левее</strong> колонки <code>AAR</code>. Если есть
                префикс или колонка «остальное», Jira запрашивается
                без фильтра <code>project in</code>. Сортировка по времени: последний комментарий, иначе{" "}
                <code>updated</code> (галочка выше может поставить «чужие» комментарии выше ваших).
              </p>
              <div
                className="jira-board"
                style={{ ["--jira-col-width" as string]: `${jiraColWidthPx}px` }}
              >
                {jiraColsSorted.map((col) => {
                  const raw = jiraByColumn.get(col.id) || [];
                  const filtered = filterJiraColumnIssues(raw, getJiraColFilters(col.id));
                  const keysLabel =
                    col.projectKeys.length > 0 ? col.projectKeys.join(", ") : "остальные проекты";
                  return (
                    <section
                      key={col.id}
                      className={"jira-column" + (jiraDragId === col.id ? " jira-column-drag" : "")}
                      onDragOver={(e) => onJiraColumnDragOver(e, col.id)}
                      onDrop={onJiraColumnDragEnd}
                    >
                      <div
                        className="jira-column-header"
                        draggable
                        onDragStart={() => onJiraColumnDragStart(col.id)}
                        onDragEnd={onJiraColumnDragEnd}
                        title="Перетащите колонку влево/вправо"
                      >
                        <div style={{ minWidth: 0 }}>
                          <div className="jira-column-title">{col.title}</div>
                          <div className="jira-column-meta">
                            {keysLabel} · карточек: {filtered.length}
                            {raw.length !== filtered.length ? ` (всего ${raw.length})` : ""}
                          </div>
                        </div>
                        <div className="jira-column-actions">
                          <button
                            type="button"
                            className="btn"
                            style={{ fontSize: "0.65rem", padding: "0.15rem 0.35rem" }}
                            onClick={() => moveJiraColumn(col.id, -1)}
                            title="Влево"
                          >
                            ◀
                          </button>
                          <button
                            type="button"
                            className="btn"
                            style={{ fontSize: "0.65rem", padding: "0.15rem 0.35rem" }}
                            onClick={() => moveJiraColumn(col.id, 1)}
                            title="Вправо"
                          >
                            ▶
                          </button>
                          <button
                            type="button"
                            className="btn"
                            style={{ fontSize: "0.65rem", padding: "0.15rem 0.35rem" }}
                            onClick={() => {
                              setEditJiraCol(col);
                              setEditJiraColTitle(col.title);
                              setEditJiraColKeys(col.projectKeys.join(", "));
                            }}
                            title="Править"
                          >
                            ✎
                          </button>
                          <button
                            type="button"
                            className="btn btn-danger"
                            style={{ fontSize: "0.65rem", padding: "0.15rem 0.35rem" }}
                            onClick={() => removeJiraColumn(col.id)}
                            title="Удалить колонку"
                          >
                            ✕
                          </button>
                        </div>
                      </div>
                      <div className="jira-column-filters">
                        <input
                          className="jira-filter-input"
                          placeholder="site ID (308, 1293…)"
                          value={getJiraColFilters(col.id).siteId}
                          onChange={(e) => setJiraColFilter(col.id, { siteId: e.target.value })}
                        />
                        <input
                          className="jira-filter-input"
                          placeholder="Поиск по тексту…"
                          value={getJiraColFilters(col.id).q}
                          onChange={(e) => setJiraColFilter(col.id, { q: e.target.value })}
                        />
                      </div>
                      <div className="jira-column-body">
                        {filtered.length === 0 && !jiraLoading ? (
                          <div className="jira-column-empty">Нет задач</div>
                    ) : null}
                        {filtered.map((row) => (
                          <article key={row.key} className="jira-card">
                            <div className="jira-card-top">
                              <a className="jira-card-key" href={row.browse_url} target="_blank" rel="noreferrer">
                                {row.key}
                              </a>
                              <span className="jira-card-status">{row.status || "—"}</span>
                            </div>
                            <div className="jira-card-summary">{row.summary || "—"}</div>
                            {row.last_comment_preview ? (
                              <div className="jira-card-comment">
                                <span className="jira-card-comment-author">
                                  {row.last_comment_author || "комментарий"}
                                </span>
                                : {row.last_comment_preview}
                              </div>
                            ) : (
                              <div className="jira-card-muted">Комментариев нет в выборке API</div>
                            )}
                            <div className="jira-card-footer">
                              <span className="jira-card-updated" title={row.updated}>
                                {row.last_comment_created
                                  ? `комм.: ${row.last_comment_created.slice(0, 16)}…`
                                  : row.updated
                                    ? `upd: ${row.updated.slice(0, 16)}…`
                                    : "—"}
                              </span>
                              <div style={{ display: "flex", gap: "0.25rem", flexWrap: "wrap" }}>
                                <button
                                  type="button"
                                  className="btn"
                                  style={{ fontSize: "0.68rem", padding: "0.15rem 0.35rem" }}
                                  title="Оставить комментарий"
                                  onClick={() => void openJiraAction(row, "comment")}
                                >
                                  Комментарий
                                </button>
                                <button
                                  type="button"
                                  className="btn"
                                  style={{ fontSize: "0.68rem", padding: "0.15rem 0.35rem" }}
                                  title="Назначить на сотрудника"
                                  onClick={() => void openJiraAction(row, "assign")}
                                >
                                  Назначить
                                </button>
                                <button
                                  type="button"
                                  className="btn"
                                  style={{ fontSize: "0.68rem", padding: "0.15rem 0.35rem" }}
                                  title="Сменить статус"
                                  onClick={() => void openJiraAction(row, "transition")}
                                >
                                  Статус
                                </button>
                                <a className="btn" href={row.browse_url} target="_blank" rel="noreferrer" style={{ fontSize: "0.68rem", padding: "0.15rem 0.35rem" }}>
                                  В Jira
                                </a>
                              </div>
                            </div>
                          </article>
                        ))}
                      </div>
                    </section>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}

      {mainTab === "time" && (
      <>
        <p className="time-board-hint">
          Один и тот же канал в двух колонках допустим, но кейс хранится один раз в БД — после синка он «привязывается» к
          колонке, с которой пришли данные. Удобнее: один канал — одна колонка; либо дублируйте осознанно.
        </p>
        <div className="board" style={{ ["--time-col-width" as string]: `${timeColWidthPx}px` }}>
        {columns.length === 0 && (
          <p style={{ color: "var(--muted)" }}>
            Добавьте колонку (канал) и нажмите «Синк». Нужен OAuth или <code>TIME_PERSONAL_ACCESS_TOKEN</code>.
          </p>
        )}
        {(timeDragList ?? timeColsSorted).map((col) => (
          <section
            key={col.id}
            className={"column" + (timeDragId === col.id ? " column-drag" : "")}
            onDragOver={(e) => onTimeColumnDragOver(e, col.id)}
            onDrop={(e) => {
              e.preventDefault();
              void onTimeColumnDragEnd();
            }}
          >
            <div className="column-header">
              <div
                className="column-header-drag"
                draggable
                onDragStart={() => onTimeColumnDragStart(col.id)}
                onDragEnd={() => void onTimeColumnDragEnd()}
                title="Перетащить колонку влево/вправо"
              >
                <div style={{ minWidth: 0 }}>
                  <div className="column-title">{col.title}</div>
                  <div className="column-meta" title={columnChannelLabel(col)}>
                    {columnChannelLabel(col)} · активных:{" "}
                    {getFilteredCasesForColumn(col.id).filter((c) => c.status === "active").length}
                  </div>
                </div>
              </div>
              <div style={{ display: "flex", gap: "0.3rem", flexShrink: 0, alignItems: "center" }}>
                <button
                  type="button"
                  className="btn"
                  style={{ fontSize: "0.65rem", padding: "0.15rem 0.35rem" }}
                  title="Влево"
                  onClick={() => moveTimeColumn(col.id, -1)}
                >
                  ◀
                </button>
                <button
                  type="button"
                  className="btn"
                  style={{ fontSize: "0.65rem", padding: "0.15rem 0.35rem" }}
                  title="Вправо"
                  onClick={() => moveTimeColumn(col.id, 1)}
                >
                  ▶
                </button>
                <button
                  type="button"
                  className="btn"
                  style={{ fontSize: "0.72rem", padding: "0.2rem 0.4rem" }}
                  title="Синхронизировать только эту колонку"
                  disabled={syncingColId === col.id || syncing}
                  onClick={() => void doColumnSync(col.id)}
                >
                  {syncingColId === col.id ? "↻…" : "↻"}
                </button>
                <button
                  type="button"
                  className="btn"
                  style={{ fontSize: "0.72rem", padding: "0.2rem 0.35rem" }}
                  title="Изменить название и каналы"
                  disabled={syncingColId === col.id || syncing}
                  onClick={() => openEditColumn(col)}
                >
                  ✎
                </button>
                <button
                  type="button"
                  className="btn"
                  style={{ fontSize: "0.75rem", padding: "0.25rem 0.45rem" }}
                  title="Фильтр: шаблоны саппорт / AMA, упоминания"
                  onClick={() => openRulesModal(col)}
                >
                  Фильтр
                </button>
                <button
                  type="button"
                  className="btn btn-danger"
                  style={{ fontSize: "0.65rem", padding: "0.15rem 0.35rem" }}
                  title="Удалить колонку"
                  onClick={() => void deleteTimeColumn(col.id)}
                >
                  ✕
                </button>
              </div>
            </div>
            <div style={{ padding: "0.4rem 0.65rem", borderBottom: "1px solid var(--card-border)", fontSize: "0.72rem", display: "flex", gap: "0.4rem", flexWrap: "wrap", alignItems: "center" }}>
              <label
                style={{ display: "flex", alignItems: "center", gap: "0.25rem", color: "var(--muted)", cursor: "pointer" }}
                title="Только треды, где первый пост написан с текущей учётки Case Board (TiMe user id). Без поиска @логина в тексте."
              >
                <input
                  type="checkbox"
                  checked={getColumnFilters(col.id).onlyMyCases}
                  onChange={(e) => setColumnFilter(col.id, { onlyMyCases: e.target.checked })}
                  style={{ margin: 0, cursor: "pointer" }}
                />
                я автор треда
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: "0.25rem", color: "var(--muted)", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={getColumnFilters(col.id).includeResolved}
                  onChange={(e) => setColumnFilter(col.id, { includeResolved: e.target.checked })}
                  style={{ margin: 0, cursor: "pointer" }}
                />
                показать решённые
              </label>
            </div>
            <div className="cards">
              {getFilteredCasesForColumn(col.id).length === 0 && (byColumn.get(col.id) || []).length === 0 ? (
                <div style={{ padding: "1.5rem 1rem", textAlign: "center", color: "var(--muted)", fontSize: "0.85rem" }}>
                  <p style={{ margin: "0 0 0.5rem" }}>Колонка пуста</p>
                  <p style={{ margin: "0 0 0.75rem", fontSize: "0.8rem" }}>
                    Новые сообщения — кнопка <b>↻</b>.
                    Старые (понедельник и раньше) — кнопка <b>Загрузить историю</b> ниже.
                  </p>
                  <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", justifyContent: "center" }}>
                    <button
                      type="button"
                      className="btn btn-primary"
                      style={{ fontSize: "0.8rem", padding: "0.4rem 0.7rem" }}
                      disabled={syncingColId === col.id}
                      onClick={() => void doHistorySyncColumn(col.id)}
                    >
                      {syncingColId === col.id ? "Загружаю…" : "Загрузить историю"}
                    </button>
                    <button
                      type="button"
                      className="btn"
                      style={{ fontSize: "0.8rem", padding: "0.4rem 0.7rem" }}
                      onClick={() => openEditColumn(col)}
                    >
                      ✎ Каналы
                    </button>
                  </div>
                </div>
              ) : getFilteredCasesForColumn(col.id).length === 0 && (byColumn.get(col.id) || []).length > 0 ? (
                <div style={{ padding: "1rem", textAlign: "center", color: "var(--muted)", fontSize: "0.85rem" }}>
                  <p style={{ margin: 0 }}>Нет кейсов по текущим фильтрам колонки</p>
                </div>
              ) : null}
              {getFilteredCasesForColumn(col.id).map((c) => (
                <article key={c.id} className="card">
                  <div className="card-top">
                    <span className="site">{c.site_id || "—"}</span>
                    <span
                      className={
                        "badge " + (c.initiator === "self" ? "badge-self" : c.initiator === "incoming" ? "badge-in" : "")
                      }
                    >
                      {initiatorLabel(c.initiator)}
                    </span>
                  </div>
                  <div style={{ fontSize: "0.78rem", color: "var(--muted)" }}>
                    {c.assignee_raw || "кому: —"} · {relTime(c.last_activity_at_ms)}
                    {c.last_message_username ? ` · @${c.last_message_username}` : ""}
                  </div>
                  {c.title_preview ? (
                    <p className="preview preview-title">{c.title_preview}</p>
                  ) : null}
                  {c.last_message_preview ? (
                    <div className={c.title_preview ? "time-card-last" : ""}>
                      {c.title_preview ? (
                        <div className="time-card-last-label">Последнее в треде</div>
                      ) : null}
                      <p className={c.title_preview ? "preview preview-last-msg" : "preview"}>
                        {c.last_message_username ? (
                          <span className="time-card-last-author">@{c.last_message_username}: </span>
                        ) : null}
                        {c.last_message_preview}
                      </p>
                    </div>
                  ) : !c.title_preview ? (
                    <p className="preview">—</p>
                  ) : null}
                  <div className="initiator-row">
                    <label>Инициатор</label>
                    <select
                      className="select-compact"
                      value={c.initiator}
                      onChange={(e) => void patchInitiator(c.id, e.target.value)}
                    >
                      <option value="unspecified">Не указан инициатор</option>
                      <option value="self">Я инициатор</option>
                      <option value="incoming">Ко мне</option>
                    </select>
                  </div>
                  <div className="card-actions">
                    <a className="btn" href={c.permalink} target="_blank" rel="noreferrer">
                      Открыть в TiMe
                    </a>
                    {c.status === "active" && (
                      <button type="button" className="btn" onClick={() => void resolveCase(c.id)}>
                        Решено
                      </button>
                    )}
                    <button type="button" className="btn btn-danger" onClick={() => void ignoreCase(c.id)}>
                      Убрать навсегда
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </section>
        ))}
      </div>
      </>
      )}

      {jiraColOpen && (
        <div className="modal-backdrop" role="presentation" onClick={() => setJiraColOpen(false)}>
          <div className="modal" role="dialog" onClick={(e) => e.stopPropagation()}>
            <h2>Новая колонка Jira</h2>
            <p style={{ fontSize: "0.82rem", color: "var(--muted)", marginTop: 0 }}>
              Точный ключ (<code>AIMP</code>) или префикс (<code>AAR*</code>, <code>FOO*</code>) — любые буквы перед{" "}
              <code>*</code>. Пустое поле = колонка «остальное».
            </p>
            <div className="field">
              <label>Название колонки</label>
              <input
                value={newJiraColTitle}
                onChange={(e) => setNewJiraColTitle(e.target.value)}
                placeholder="Напр. AIMP + ATM"
              />
            </div>
            <div className="field">
              <label>Ключи проектов</label>
              <input
                value={newJiraColKeys}
                onChange={(e) => setNewJiraColKeys(e.target.value)}
                placeholder="AIMP, AAR*, FOO* или пусто = остальное"
              />
            </div>
            <div className="row">
              <button type="button" className="btn btn-primary" onClick={() => addJiraColumn()}>
                Добавить
              </button>
              <button type="button" className="btn" onClick={() => setJiraColOpen(false)}>
                Отмена
              </button>
            </div>
          </div>
        </div>
      )}

      {editJiraCol && (
        <div className="modal-backdrop" role="presentation" onClick={() => setEditJiraCol(null)}>
          <div className="modal" role="dialog" onClick={(e) => e.stopPropagation()}>
            <h2>Колонка Jira</h2>
            <div className="field">
              <label>Название</label>
              <input value={editJiraColTitle} onChange={(e) => setEditJiraColTitle(e.target.value)} />
            </div>
            <div className="field">
              <label>Ключи проектов (через запятую)</label>
              <input
                value={editJiraColKeys}
                onChange={(e) => setEditJiraColKeys(e.target.value)}
                placeholder="AIMP, AAR*, X*"
              />
              <p style={{ margin: "0.35rem 0 0", fontSize: "0.76rem", color: "var(--muted)" }}>
                Префикс: буквы + * в конце (любая длина префикса).
              </p>
            </div>
            <div className="row">
              <button type="button" className="btn btn-primary" onClick={() => saveEditJiraColumn()}>
                Сохранить
              </button>
              <button type="button" className="btn" onClick={() => setEditJiraCol(null)}>
                Отмена
              </button>
            </div>
          </div>
      </div>
      )}

      {colOpen && (
        <div className="modal-backdrop" role="presentation" onClick={() => setColOpen(false)}>
          <div className="modal" role="dialog" onClick={(e) => e.stopPropagation()}>
            <h2>Новая колонка</h2>
            <div className="field">
              <label>Название</label>
              <input value={colTitle} onChange={(e) => setColTitle(e.target.value)} placeholder="Саппорт" />
            </div>
            <div className="field">
              <label>URL каналов</label>
              <textarea
                rows={5}
                value={colUrl}
                onChange={(e) => setColUrl(e.target.value)}
                placeholder={
                  "Один или несколько полных URL — по одному на строку, например:\n" +
                  "https://time.tbank.ru/tinkoff/channels/tech\n" +
                  "https://time.tbank.ru/tinkoff/channels/alerts"
                }
                style={{ width: "100%", fontFamily: "inherit", fontSize: "0.9rem", resize: "vertical" }}
              />
              <p style={{ margin: "0.35rem 0 0", fontSize: "0.78rem", color: "var(--muted)" }}>
                Все эти каналы соберутся в одну колонку (удобно, если вас тегают в разных чатах).
              </p>
            </div>
            <div className="row">
              <button type="button" className="btn btn-primary" onClick={() => void addColumn()}>
                Создать
              </button>
              <button type="button" className="btn" onClick={() => setColOpen(false)}>
                Отмена
              </button>
            </div>
          </div>
        </div>
      )}

      {editCol && (
        <div className="modal-backdrop" role="presentation" onClick={() => setEditCol(null)}>
          <div className="modal" role="dialog" onClick={(e) => e.stopPropagation()}>
            <h2>Изменить колонку</h2>
            <div className="field">
              <label>Название</label>
              <input value={editColTitle} onChange={(e) => setEditColTitle(e.target.value)} placeholder="Tech" />
            </div>
            <div className="field">
              <label>URL каналов (по одному на строку)</label>
              <textarea
                rows={6}
                value={editColUrls}
                onChange={(e) => setEditColUrls(e.target.value)}
                style={{ width: "100%", fontFamily: "inherit", fontSize: "0.9rem", resize: "vertical" }}
              />
            </div>
            <div className="row">
              <button type="button" className="btn btn-primary" onClick={() => void saveEditColumn()}>
                Сохранить
              </button>
              <button type="button" className="btn" onClick={() => setEditCol(null)}>
                Отмена
              </button>
            </div>
          </div>
        </div>
      )}

      {rulesColId && (
        <div className="modal-backdrop" role="presentation" onClick={() => setRulesColId(null)}>
          <div className="modal" role="dialog" onClick={(e) => e.stopPropagation()}>
            <h2>Фильтр колонки</h2>
            <label className="row" style={{ alignItems: "center", fontSize: "0.9rem", marginBottom: "0.75rem", gap: "0.5rem" }}>
              <input
                type="checkbox"
                checked={rulesSelfOnly}
                onChange={(e) => setRulesSelfOnly(e.target.checked)}
              />
              <span>
                <strong>Канал с моими тредами</strong>
                <span style={{ color: "var(--muted)", fontSize: "0.8rem" }}>
                  {" "}
                  — в доску попадают только треды, где <strong>первый пост</strong> написан с учётки, под которой вы вошли в Case Board
                  (по TiMe user id). <strong>Текст поста не сканируется</strong> на ваш @логин.
                </span>
              </span>
            </label>
            {!rulesSelfOnly && (
              <>
                <p style={{ fontSize: "0.82rem", color: "var(--muted)", marginTop: 0 }}>
                  Режим «саппорт»: отбор по шаблону и обращению к вам. Ниже — доп. подстроки в тексте (например тег канала); это{" "}
                  <strong>не</strong> ваш логин из Case Board, только то, что вы сами перечислите.
                </p>
                <div className="field">
                  <label>Подстроки в тексте корня (через запятую)</label>
                  <textarea
                    rows={3}
                    value={rulesReporter}
                    onChange={(e) => setRulesReporter(e.target.value)}
                    placeholder="@any_2nd_line, фраза из шаблона — по желанию; «Подставить из TiMe» добавляет ваши @ и имя для саппорт-тредов"
                  />
                  <div className="row" style={{ marginTop: "0.35rem" }}>
                    <button type="button" className="btn" style={{ fontSize: "0.8rem" }} onClick={() => void insertMyReporterFromTimeProfile()}>
                      Подставить мой логин и имя из TiMe
                    </button>
                  </div>
                </div>
                <label className="row" style={{ alignItems: "center", fontSize: "0.88rem", marginBottom: "0.5rem" }}>
                  <input
                    type="checkbox"
                    checked={rulesRequireAddressed}
                    onChange={(e) => setRulesRequireAddressed(e.target.checked)}
                  />
                  Только обращения ко мне (@упоминание / mentions / доп. имена)
                </label>
                <label className="row" style={{ alignItems: "center", fontSize: "0.88rem", marginBottom: "0.65rem" }}>
                  <input
                    type="checkbox"
                    checked={rulesIntro}
                    onChange={(e) => setRulesIntro(e.target.checked)}
                  />
                  Только с «Обращение в саппорт» (для формы саппорта)
                </label>
              </>
            )}
            <div className="row">
              <button type="button" className="btn btn-primary" onClick={() => void saveColumnRules()}>
                Сохранить
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={rulesPruning}
                onClick={() => void pruneColumn()}
              >
                {rulesPruning ? "…" : "Убрать неподходящие с доски"}
              </button>
              <button type="button" className="btn" onClick={() => setRulesColId(null)}>
                Отмена
              </button>
            </div>
            <p style={{ fontSize: "0.78rem", color: "var(--muted)", marginBottom: 0 }}>
              После смены правил — «Синк» или ↻ по колонке; старые свои треды — «Загрузить историю». Старые карточки без автора в БД
              подтянут user id при следующем синке.
            </p>
          </div>
        </div>
      )}

      {/* Jira action modal */}
      {jiraActionIssue && jiraActionType && (
        <div className="modal-backdrop" role="presentation" onClick={closeJiraAction}>
          <div className="modal" role="dialog" style={{ maxWidth: "520px" }} onClick={(e) => e.stopPropagation()}>
            <h2 style={{ marginTop: 0 }}>
              {jiraActionType === "comment" && `Комментарий — ${jiraActionIssue.key}`}
              {jiraActionType === "assign" && `Назначить — ${jiraActionIssue.key}`}
              {jiraActionType === "transition" && `Сменить статус — ${jiraActionIssue.key}`}
            </h2>
            <p style={{ margin: "0 0 0.75rem", fontSize: "0.82rem", color: "var(--muted)" }}>
              {jiraActionIssue.summary}
            </p>

            {jiraActionType === "comment" && (
              <>
                <div className="field">
                  <label>Текст комментария</label>
                  <textarea
                    rows={5}
                    autoFocus
                    value={jiraActionComment}
                    onChange={(e) => setJiraActionComment(e.target.value)}
                    placeholder="Введите комментарий…"
                    style={{ width: "100%", boxSizing: "border-box" }}
                  />
                </div>
                <div className="field">
                  <label>Прикрепить изображения (необязательно)</label>
                  <input
                    type="file"
                    accept="image/*"
                    multiple
                    onChange={(e) => setJiraActionFiles(Array.from(e.target.files || []))}
                  />
                  {jiraActionFiles.length > 0 && (
                    <p style={{ margin: "0.3rem 0 0", fontSize: "0.78rem", color: "var(--muted)" }}>
                      Выбрано: {jiraActionFiles.map((f) => f.name).join(", ")}
                    </p>
                  )}
                </div>
                {jiraActionError && <p style={{ color: "var(--danger, #e55)", fontSize: "0.82rem" }}>{jiraActionError}</p>}
                <div className="row">
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={jiraActionSubmitting || !jiraActionComment.trim()}
                    onClick={() => void submitJiraComment()}
                  >
                    {jiraActionSubmitting ? "Отправка…" : "Отправить"}
                  </button>
                  <button type="button" className="btn" onClick={closeJiraAction}>Отмена</button>
                </div>
              </>
            )}

            {jiraActionType === "assign" && (
              <>
                <div className="field">
                  <label>Поиск сотрудника (логин или имя)</label>
                  <input
                    autoFocus
                    value={jiraActionAssigneeQuery}
                    onChange={(e) => searchJiraUsers(e.target.value)}
                    placeholder="Введите имя или логин…"
                  />
                </div>
                {jiraUserResults.length > 0 && (
                  <div style={{ border: "1px solid var(--border)", borderRadius: "4px", marginBottom: "0.75rem", maxHeight: "180px", overflowY: "auto" }}>
                    {jiraUserResults.map((u) => (
                      <button
                        key={u.name}
                        type="button"
                        style={{
                          display: "block",
                          width: "100%",
                          textAlign: "left",
                          padding: "0.4rem 0.6rem",
                          border: "none",
                          background: jiraActionAssigneeName === u.name ? "var(--accent, #0a84ff22)" : "transparent",
                          cursor: "pointer",
                          color: "var(--text)",
                          fontSize: "0.85rem",
                        }}
                        onClick={() => {
                          setJiraActionAssigneeName(u.name);
                          setJiraActionAssigneeQuery(u.displayName || u.name);
                          setJiraUserResults([]);
                        }}
                      >
                        <strong>{u.displayName}</strong>{" "}
                        <span style={{ color: "var(--muted)", fontSize: "0.78rem" }}>({u.name})</span>
                      </button>
                    ))}
                  </div>
                )}
                {jiraActionAssigneeName && (
                  <p style={{ fontSize: "0.82rem", margin: "0 0 0.6rem" }}>
                    Выбран: <strong>{jiraActionAssigneeQuery}</strong>
                    <button
                      type="button"
                      className="btn"
                      style={{ marginLeft: "0.5rem", fontSize: "0.7rem", padding: "0.1rem 0.3rem" }}
                      onClick={() => { setJiraActionAssigneeName(""); setJiraActionAssigneeQuery(""); }}
                    >✕</button>
                  </p>
                )}
                {jiraActionError && <p style={{ color: "var(--danger, #e55)", fontSize: "0.82rem" }}>{jiraActionError}</p>}
                <div className="row">
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={jiraActionSubmitting || !jiraActionAssigneeName}
                    onClick={() => void submitJiraAssign()}
                  >
                    {jiraActionSubmitting ? "…" : "Назначить"}
                  </button>
                  <button type="button" className="btn" onClick={closeJiraAction}>Отмена</button>
                </div>
              </>
            )}

            {jiraActionType === "transition" && (
              <>
                {jiraActionTransitions.length === 0 && !jiraActionError && (
                  <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>Загрузка переходов…</p>
                )}
                {jiraActionTransitions.length > 0 && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginBottom: "0.75rem" }}>
                    {jiraActionTransitions.map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        className={"btn" + (t.name === "To Do" ? " btn-primary" : "")}
                        disabled={jiraActionSubmitting}
                        onClick={() => void submitJiraTransition(t.id, t.name)}
                      >
                        {t.name}
                      </button>
                    ))}
                  </div>
                )}
                {jiraActionError && <p style={{ color: "var(--danger, #e55)", fontSize: "0.82rem" }}>{jiraActionError}</p>}
                <div className="row">
                  <button type="button" className="btn" onClick={closeJiraAction}>Закрыть</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Notification settings modal */}
      {notifOpen && (
        <div className="modal-backdrop" role="presentation" onClick={() => setNotifOpen(false)}>
          <div
            className="modal"
            role="dialog"
            style={{ maxWidth: "580px", maxHeight: "85vh", overflowY: "auto" }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 style={{ marginTop: 0 }}>Уведомления Jira → Time</h2>
            <p style={{ fontSize: "0.82rem", color: "var(--muted)", margin: "0 0 1rem" }}>
              Новые комментарии на отслеживаемых задачах будут приходить в выбранный канал Time.
            </p>

            <div className="field">
              <label>URL канала Time (куда слать уведомления)</label>
              <input
                value={notifChannelUrl}
                onChange={(e) => setNotifChannelUrl(e.target.value)}
                placeholder="https://time.tbank.ru/team/channels/my-channel"
              />
              {notifSettings?.time_channel_id && (
                <p style={{ margin: "0.25rem 0 0", fontSize: "0.75rem", color: "var(--muted)" }}>
                  channel_id: {notifSettings.time_channel_id}
                </p>
              )}
            </div>

            <div className="field">
              <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <input
                  type="checkbox"
                  checked={notifEnabled}
                  onChange={(e) => setNotifEnabled(e.target.checked)}
                />
                Включить уведомления
              </label>
            </div>

            <div className="field">
              <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <input
                  type="checkbox"
                  checked={notifComments}
                  onChange={(e) => setNotifComments(e.target.checked)}
                />
                Уведомлять о новых комментариях
              </label>
            </div>

            <div className="field">
              <label>Интервал проверки (сек, мин 60 — макс 3600)</label>
              <input
                type="number"
                min={60}
                max={3600}
                value={notifInterval}
                onChange={(e) => setNotifInterval(Math.max(60, Math.min(3600, parseInt(e.target.value) || 180)))}
                style={{ width: "100px" }}
              />
            </div>

            {notifError && <p style={{ color: "var(--danger, #e55)", fontSize: "0.82rem" }}>{notifError}</p>}
            {notifTestMsg && <p style={{ color: "var(--accent2, #4caf50)", fontSize: "0.82rem" }}>{notifTestMsg}</p>}

            <div className="row" style={{ marginBottom: "1.25rem" }}>
              <button
                type="button"
                className="btn btn-primary"
                disabled={notifSaving}
                onClick={() => void saveNotifSettings()}
              >
                {notifSaving ? "Сохранение…" : "Сохранить"}
              </button>
              <button
                type="button"
                className="btn"
                disabled={!notifSettings?.time_channel_id}
                onClick={() => void testNotification()}
                title="Отправит тестовое сообщение в настроенный канал"
              >
                Тест
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => void api("/api/notifications/poll", { method: "POST" }).then(() => setNotifTestMsg("Проверка запущена")).catch((e) => setNotifError(String(e)))}
                title="Запустить проверку прямо сейчас"
              >
                Проверить сейчас
              </button>
            </div>

            <h3 style={{ margin: "0 0 0.6rem", fontSize: "0.95rem" }}>Отслеживаемые JQL-запросы</h3>
            <p style={{ fontSize: "0.78rem", color: "var(--muted)", margin: "0 0 0.75rem" }}>
              Добавьте один или несколько JQL — все задачи в выборке будут отслеживаться на новые комментарии.
            </p>

            {jiraWatchers.length > 0 && (
              <div style={{ marginBottom: "0.75rem" }}>
                {jiraWatchers.map((w) => (
                  <div
                    key={w.id}
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: "0.5rem",
                      padding: "0.4rem 0",
                      borderBottom: "1px solid var(--border)",
                      fontSize: "0.82rem",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={w.enabled}
                      style={{ marginTop: "0.15rem" }}
                      onChange={(e) => void toggleJiraWatcher(w.id, e.target.checked)}
                    />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      {w.label && <div style={{ fontWeight: 600 }}>{w.label}</div>}
                      <div style={{ color: "var(--muted)", wordBreak: "break-all" }}>{w.jql}</div>
                    </div>
                    <button
                      type="button"
                      className="btn btn-danger"
                      style={{ fontSize: "0.7rem", padding: "0.1rem 0.3rem", flexShrink: 0 }}
                      onClick={() => void deleteJiraWatcher(w.id)}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem", marginBottom: "0.75rem" }}>
              <input
                value={newWatcherLabel}
                onChange={(e) => setNewWatcherLabel(e.target.value)}
                placeholder="Название (необязательно, напр. «Мои задачи»)"
              />
              <textarea
                rows={2}
                value={newWatcherJql}
                onChange={(e) => setNewWatcherJql(e.target.value)}
                placeholder="JQL, напр.: reporter = currentUser() AND statusCategory != Done"
                style={{ width: "100%", boxSizing: "border-box" }}
              />
              <select
                value={newWatcherType}
                onChange={(e) => setNewWatcherType(e.target.value)}
                style={{ width: "fit-content" }}
              >
                <option value="custom">Произвольный</option>
                <option value="reporter">Мои задачи (reporter)</option>
                <option value="assignee">Назначены на меня (assignee)</option>
                <option value="watcher">Я слежу (watcher)</option>
              </select>
              <div>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={!newWatcherJql.trim()}
                  onClick={() => void addJiraWatcher()}
                >
                  + Добавить
                </button>
              </div>
            </div>

            <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.75rem" }}>
              <p style={{ fontSize: "0.75rem", color: "var(--muted)", margin: 0 }}>
                Готовые JQL: <code>reporter = currentUser() AND statusCategory != Done</code> (создал я) ·{" "}
                <code>watcher = currentUser() AND statusCategory != Done</code> (слежу)
              </p>
            </div>

            <div className="row" style={{ marginTop: "1rem" }}>
              <button type="button" className="btn" onClick={() => setNotifOpen(false)}>Закрыть</button>
            </div>
          </div>
        </div>
      )}

      {threadOpen && (
        <div className="modal-backdrop" role="presentation" onClick={() => setThreadOpen(false)}>
          <div className="modal" role="dialog" onClick={(e) => e.stopPropagation()}>
            <h2>Добавить тред</h2>
            <div className="field">
              <label>Колонка</label>
              <select
                value={threadCol || (columns.length > 0 ? columns[0].id : "")}
                onChange={(e) => setThreadCol(e.target.value)}
              >
                {columns.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.title}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Ссылка на сообщение</label>
              <input
                autoFocus
                value={threadUrl}
                onChange={(e) => setThreadUrl(e.target.value)}
                placeholder="https://time.tbank.ru/tinkoff/pl/…postId…"
              />
            </div>
            <div className="field">
              <label>Название (необязательно — если пусто, берётся из текста поста)</label>
              <input
                value={threadTitle}
                onChange={(e) => setThreadTitle(e.target.value)}
                placeholder="Краткое описание кейса"
              />
            </div>
            <div className="field">
              <label>Инициатор</label>
              <select value={threadInit} onChange={(e) => setThreadInit(e.target.value)}>
                <option value="unspecified">Не указан инициатор</option>
                <option value="self">Я инициатор</option>
                <option value="incoming">Ко мне</option>
              </select>
            </div>
            <div className="row">
              <button type="button" className="btn btn-primary" onClick={() => void addThread()}>
                Добавить
              </button>
              <button type="button" className="btn" onClick={() => setThreadOpen(false)}>
                Отмена
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
