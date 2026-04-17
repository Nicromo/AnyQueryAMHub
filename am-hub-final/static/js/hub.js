/**
 * hub.js — общий скрипт для всех страниц AM Hub
 * Подключить в каждый шаблон: <script src="/static/js/hub.js"></script>
 *
 * Предоставляет:
 *  - Toast-уведомления (window.toast)
 *  - Колокольчик уведомлений в сайдбаре
 *  - Переключатель темы
 *  - Мобильное меню (hamburger)
 *  - Глобальный поиск (Cmd/Ctrl+K)
 */

// ─── THEME ────────────────────────────────────────────────────────────────────
(function () {
  const saved = localStorage.getItem('theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
})();

function toggleTheme() {
  const h = document.documentElement;
  const next = h.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  h.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
}

// ─── TOAST ────────────────────────────────────────────────────────────────────
(function () {
  const style = document.createElement('style');
  style.textContent = `
    #toast-container {
      position: fixed; bottom: 24px; right: 24px;
      z-index: 9999; display: flex; flex-direction: column; gap: 8px;
      pointer-events: none;
    }
    .toast {
      display: flex; align-items: center; gap: 10px;
      padding: 12px 16px; border-radius: 10px;
      font-family: 'Inter', sans-serif; font-size: .85rem; font-weight: 500;
      box-shadow: 0 8px 24px rgba(0,0,0,.3);
      pointer-events: auto; cursor: default;
      animation: toastIn .25s ease; max-width: 340px;
      border: 1px solid rgba(255,255,255,.08);
    }
    .toast.out { animation: toastOut .25s ease forwards; }
    .toast-success { background: #0d2218; color: #4ade80; border-color: rgba(74,222,128,.2); }
    .toast-error   { background: #2a0d0d; color: #f87171; border-color: rgba(248,113,113,.2); }
    .toast-info    { background: #0d1a2a; color: #93c5fd; border-color: rgba(147,197,253,.2); }
    .toast-warning { background: #2a1a00; color: #fbbf24; border-color: rgba(251,191,36,.2); }
    [data-theme="light"] .toast-success { background: #f0fdf4; color: #16a34a; }
    [data-theme="light"] .toast-error   { background: #fef2f2; color: #dc2626; }
    [data-theme="light"] .toast-info    { background: #eff6ff; color: #2563eb; }
    [data-theme="light"] .toast-warning { background: #fffbeb; color: #d97706; }
    @keyframes toastIn  { from { opacity:0; transform:translateY(12px); } to { opacity:1; transform:none; } }
    @keyframes toastOut { to   { opacity:0; transform:translateY(12px); } }
  `;
  document.head.appendChild(style);

  let container;
  function getContainer() {
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      document.body.appendChild(container);
    }
    return container;
  }

  const ICONS = { success: '✅', error: '❌', info: 'ℹ️', warning: '⚠️' };

  window.toast = function (message, type = 'info', duration = 3500) {
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.innerHTML = `<span>${ICONS[type] || ''}</span><span>${message}</span>`;
    getContainer().appendChild(el);

    const remove = () => {
      el.classList.add('out');
      el.addEventListener('animationend', () => el.remove(), { once: true });
    };
    const timer = setTimeout(remove, duration);
    el.addEventListener('click', () => { clearTimeout(timer); remove(); });
  };
})();

// ─── NOTIFICATIONS BELL ───────────────────────────────────────────────────────
(function () {
  async function loadNotifications() {
    const bell = document.getElementById('notif-bell');
    const badge = document.getElementById('notif-badge');
    const panel = document.getElementById('notif-panel');
    if (!bell) return;

    try {
      const r = await fetch('/api/notifications');
      if (!r.ok) return;
      const data = await r.json();
      const notifs = data.notifications || [];
      const high = notifs.filter(n => n.priority === 'high').length;
      const count = high || notifs.length;

      if (badge) {
        badge.textContent = count > 0 ? (count > 9 ? '9+' : count) : '';
        badge.style.display = count > 0 ? 'flex' : 'none';
      }

      if (panel) {
        if (!notifs.length) {
          panel.innerHTML = '<div class="notif-empty">✅ Всё под контролем</div>';
          return;
        }
        panel.innerHTML = notifs.slice(0, 10).map(n => {
          const icon = n.priority === 'high' ? '🔴' : '🟡';
          const url = n.client_id ? `/client/${n.client_id}` : '#';
          return `<a class="notif-item" href="${url}">
            <span class="notif-icon">${icon}</span>
            <span class="notif-text">${n.message}</span>
          </a>`;
        }).join('');
        if (notifs.length > 10) {
          panel.innerHTML += `<div class="notif-more">И ещё ${notifs.length - 10}... <a href="/inbox">Показать все</a></div>`;
        }
      }
    } catch (e) {
      // silent
    }
  }

  // Запускаем после загрузки DOM
  document.addEventListener('DOMContentLoaded', () => {
    loadNotifications();
    // Обновляем каждые 2 минуты
    setInterval(loadNotifications, 120_000);
  });
})();

// ─── MOBILE SIDEBAR ───────────────────────────────────────────────────────────
(function () {
  const style = document.createElement('style');
  style.textContent = `
    .hamburger {
      display: none; position: fixed; top: 14px; left: 14px; z-index: 300;
      background: var(--card); border: 1px solid var(--border);
      border-radius: 8px; padding: 8px 10px; cursor: pointer; font-size: 1rem;
    }
    .sidebar-overlay {
      display: none; position: fixed; inset: 0; background: rgba(0,0,0,.5);
      z-index: 149; backdrop-filter: blur(2px);
    }
    @media (max-width: 768px) {
      .hamburger { display: block; }
      .sidebar { transform: translateX(-100%); transition: transform .25s ease; z-index: 150; }
      .sidebar.open { transform: none; }
      .sidebar-overlay.open { display: block; }
      .main { margin-left: 0 !important; padding-top: 56px !important; }
    }
  `;
  document.head.appendChild(style);

  document.addEventListener('DOMContentLoaded', () => {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar) return;

    // Hamburger button
    const btn = document.createElement('button');
    btn.className = 'hamburger';
    btn.textContent = '☰';
    document.body.appendChild(btn);

    // Overlay
    const overlay = document.createElement('div');
    overlay.className = 'sidebar-overlay';
    document.body.appendChild(overlay);

    const open = () => { sidebar.classList.add('open'); overlay.classList.add('open'); };
    const close = () => { sidebar.classList.remove('open'); overlay.classList.remove('open'); };

    btn.addEventListener('click', open);
    overlay.addEventListener('click', close);
    sidebar.querySelectorAll('.nav-item').forEach(a => a.addEventListener('click', close));
  });
})();

// ─── GLOBAL SEARCH (Cmd/Ctrl+K) ───────────────────────────────────────────────
(function () {
  const style = document.createElement('style');
  style.textContent = `
    #search-modal {
      display: none; position: fixed; inset: 0; z-index: 1000;
      background: rgba(0,0,0,.6); backdrop-filter: blur(4px);
      align-items: flex-start; justify-content: center; padding-top: 80px;
    }
    #search-modal.open { display: flex; }
    #search-box {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 14px; width: 100%; max-width: 560px;
      box-shadow: 0 24px 64px rgba(0,0,0,.4); overflow: hidden;
    }
    #search-input {
      width: 100%; padding: 16px 20px; background: transparent;
      border: none; outline: none; color: var(--text);
      font-size: 1rem; border-bottom: 1px solid var(--border);
    }
    #search-results { max-height: 360px; overflow-y: auto; }
    .search-result {
      display: flex; align-items: center; gap: 12px;
      padding: 12px 20px; text-decoration: none; color: var(--text);
      border-bottom: 1px solid var(--border); transition: background .1s;
    }
    .search-result:hover, .search-result.focused { background: rgba(99,102,241,.1); }
    .search-result-icon { font-size: 1rem; width: 24px; text-align: center; flex-shrink: 0; }
    .search-result-title { font-size: .88rem; font-weight: 500; }
    .search-result-sub { font-size: .75rem; color: var(--muted); margin-top: 1px; }
    .search-empty { padding: 24px; text-align: center; color: var(--muted); font-size: .85rem; }
    .search-hint { padding: 10px 20px; font-size: .72rem; color: var(--muted);
      display: flex; gap: 16px; }
    .search-hint kbd {
      background: rgba(255,255,255,.08); border-radius: 4px;
      padding: 1px 5px; font-family: monospace; font-size: .7rem;
    }
  `;
  document.head.appendChild(style);

  document.addEventListener('DOMContentLoaded', () => {
    const modal = document.createElement('div');
    modal.id = 'search-modal';
    modal.innerHTML = `
      <div id="search-box">
        <input id="search-input" placeholder="Поиск клиентов, задач, встреч..." autocomplete="off">
        <div id="search-results"></div>
        <div class="search-hint">
          <span><kbd>↑↓</kbd> навигация</span>
          <span><kbd>Enter</kbd> открыть</span>
          <span><kbd>Esc</kbd> закрыть</span>
        </div>
      </div>`;
    document.body.appendChild(modal);

    const input = document.getElementById('search-input');
    const results = document.getElementById('search-results');
    let debounceTimer, focused = -1;

    const open = () => {
      modal.classList.add('open');
      input.value = '';
      results.innerHTML = '';
      focused = -1;
      setTimeout(() => input.focus(), 50);
    };
    const close = () => { modal.classList.remove('open'); };

    modal.addEventListener('click', e => { if (e.target === modal) close(); });
    document.addEventListener('keydown', e => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); open(); return; }
      if (!modal.classList.contains('open')) return;
      if (e.key === 'Escape') { close(); return; }

      const items = results.querySelectorAll('.search-result');
      if (e.key === 'ArrowDown') { e.preventDefault(); setFocus(items, Math.min(focused + 1, items.length - 1)); }
      if (e.key === 'ArrowUp')   { e.preventDefault(); setFocus(items, Math.max(focused - 1, 0)); }
      if (e.key === 'Enter' && focused >= 0) { items[focused]?.click(); close(); }
    });

    function setFocus(items, idx) {
      items.forEach((el, i) => el.classList.toggle('focused', i === idx));
      focused = idx;
      items[idx]?.scrollIntoView({ block: 'nearest' });
    }

    input.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      const q = input.value.trim();
      if (!q) { results.innerHTML = ''; return; }
      debounceTimer = setTimeout(() => doSearch(q), 200);
    });

    async function doSearch(q) {
      results.innerHTML = '<div class="search-empty">⏳ Ищем...</div>';
      try {
        const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=12`);
        const data = await r.json();
        const items = data.results || [];
        focused = -1;

        if (!items.length) {
          results.innerHTML = `<div class="search-empty">Ничего не найдено по «${q}»</div>`;
          return;
        }

        const TYPE_ICON = { client: '👤', task: '✅', meeting: '📅', note: '📝' };
        results.innerHTML = items.map((item, i) => `
          <a class="search-result" href="${item.url || '#'}" data-idx="${i}">
            <div class="search-result-icon">${TYPE_ICON[item.type] || '🔍'}</div>
            <div>
              <div class="search-result-title">${item.title}</div>
              ${item.subtitle ? `<div class="search-result-sub">${item.subtitle}</div>` : ''}
            </div>
          </a>`).join('');

        results.querySelectorAll('.search-result').forEach(el => {
          el.addEventListener('click', close);
        });
      } catch (e) {
        results.innerHTML = '<div class="search-empty">❌ Ошибка поиска</div>';
      }
    }
  });
})();

// ─── SIDEBAR NOTIFICATIONS STYLES ─────────────────────────────────────────────
(function () {
  const style = document.createElement('style');
  style.textContent = `
    .notif-bell-wrap { position: relative; display: inline-flex; }
    #notif-badge {
      position: absolute; top: -4px; right: -4px;
      background: var(--red, #ef4444); color: #fff;
      font-size: .6rem; font-weight: 700; border-radius: 20px;
      min-width: 16px; height: 16px; display: none;
      align-items: center; justify-content: center; padding: 0 3px;
    }
    #notif-panel-wrap {
      position: fixed; left: 248px; bottom: 60px;
      background: var(--card); border: 1px solid var(--border);
      border-radius: 12px; width: 320px; box-shadow: 0 16px 48px rgba(0,0,0,.3);
      z-index: 200; display: none; overflow: hidden;
    }
    #notif-panel-wrap.open { display: block; }
    #notif-panel-title {
      padding: 12px 16px; font-size: .8rem; font-weight: 600;
      border-bottom: 1px solid var(--border); color: var(--muted);
      text-transform: uppercase; letter-spacing: .05em;
    }
    #notif-panel { max-height: 320px; overflow-y: auto; }
    .notif-item {
      display: flex; gap: 10px; padding: 10px 16px;
      text-decoration: none; color: var(--text); font-size: .82rem;
      border-bottom: 1px solid var(--border); transition: background .1s;
    }
    .notif-item:hover { background: rgba(99,102,241,.08); }
    .notif-icon { flex-shrink: 0; }
    .notif-text { line-height: 1.4; }
    .notif-empty { padding: 20px; text-align: center; color: var(--muted); font-size: .83rem; }
    .notif-more { padding: 8px 16px; font-size: .76rem; color: var(--muted); text-align: center; }
    @media (max-width: 768px) {
      #notif-panel-wrap { left: 12px; right: 12px; width: auto; bottom: 70px; }
    }
  `;
  document.head.appendChild(style);

  document.addEventListener('DOMContentLoaded', () => {
    const bellItem = document.getElementById('notif-bell');
    if (!bellItem) return;

    const wrap = document.createElement('div');
    wrap.id = 'notif-panel-wrap';
    wrap.innerHTML = '<div id="notif-panel-title">🔔 Уведомления</div><div id="notif-panel"></div>';
    document.body.appendChild(wrap);

    bellItem.addEventListener('click', e => {
      e.preventDefault();
      wrap.classList.toggle('open');
    });

    document.addEventListener('click', e => {
      if (!bellItem.contains(e.target) && !wrap.contains(e.target)) {
        wrap.classList.remove('open');
      }
    });
  });
})();
