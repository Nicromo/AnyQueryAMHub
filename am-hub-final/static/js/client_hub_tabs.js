// client_hub_tabs.js — реализация вкладок Notes/Roadmap/QBR/Meetings/Rules/Feeds
// Подключается в templates/client_detail.html после определения CID и api().
// Переопределяет loadNotes/loadRoadmap/loadQBR/loadMeetings/loadRules/loadFeeds.
//
// Требует существования глобальных: CID, api, $, esc, fmtDate, fmtMoney.

(function () {
  const CID = document.querySelector('[data-client-id]').dataset.clientId;
  const api = (p, o = {}) => fetch(p, {credentials: 'include', ...o}).then(r => r.ok ? r.json() : Promise.reject(r));
  const $ = s => document.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmtDate = d => d ? new Date(d).toLocaleDateString('ru') : '—';
  const fmtDT = d => d ? new Date(d).toLocaleString('ru', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';

  // ──────── NOTES ────────
  window.loadNotes = async function () {
    const c = $('#tab-notes');
    c.innerHTML = `
      <div class="mb-3">
        <textarea id="note-input" class="w-full bg-slate-900 rounded-lg p-2 text-sm" rows="3" placeholder="Новая заметка..."></textarea>
        <button onclick="addNote()" class="mt-2 px-3 py-1.5 rounded-lg bg-indigo-600 text-sm">+ Добавить заметку</button>
      </div>
      <div id="notes-list" class="space-y-2"></div>`;
    try {
      const d = await api(`/api/clients/${CID}/notes`).catch(() => ({notes: []}));
      const notes = d.notes || d || [];
      $('#notes-list').innerHTML = notes.length ? notes.map(n => `
        <div class="bg-slate-900 rounded-lg p-3 ${n.is_pinned ? 'border-l-4 border-amber-500' : ''}">
          <div class="flex items-start gap-2">
            <div class="flex-1 text-sm">${esc(n.content)}</div>
            <button onclick="pinNote(${n.id}, ${!n.is_pinned})" class="text-xs text-slate-500 hover:text-amber-400">${n.is_pinned ? '📌 откр.' : '📌 pin'}</button>
          </div>
          <div class="text-xs text-slate-500 mt-1">${fmtDate(n.created_at)}</div>
        </div>`).join('') : '<div class="text-slate-500 text-sm">Заметок пока нет</div>';
    } catch (e) { $('#notes-list').innerHTML = '<div class="text-red-400">Ошибка загрузки</div>'; }
  };
  window.addNote = async function () {
    const content = $('#note-input').value.trim();
    if (!content) return;
    try {
      await api(`/api/clients/${CID}/notes`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content})});
      $('#note-input').value = '';
      loadNotes();
    } catch (e) { alert('Ошибка создания заметки — возможно эндпоинт /api/clients/{id}/notes ещё не готов'); }
  };
  window.pinNote = async function (id, pinned) {
    try {
      await api(`/api/clients/${CID}/notes/${id}/pin`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({pinned})});
      loadNotes();
    } catch (e) { alert('Ошибка pin'); }
  };

  // ──────── ROADMAP (kanban 5 колонок) ────────
  window.loadRoadmap = async function () {
    const c = $('#tab-roadmap');
    const cols = [['plan','План'],['in_progress','В работе'],['blocked','Блок'],['review','Ревью'],['done','Готово']];
    c.innerHTML = `<div class="grid grid-cols-2 md:grid-cols-5 gap-3">${
      cols.map(([k,t]) => `<div class="bg-slate-900 rounded-lg p-2 min-h-[200px]"><div class="text-xs text-slate-400 mb-2">${t}</div><div id="col-${k}" class="space-y-2"></div></div>`).join('')
    }</div>`;
    try {
      const tasks = await api(`/api/tasks?client_id=${CID}`).catch(() => []);
      const list = Array.isArray(tasks) ? tasks : (tasks.tasks || []);
      const byStatus = {plan:[], in_progress:[], blocked:[], review:[], done:[]};
      list.forEach(t => (byStatus[t.status] || byStatus.plan).push(t));
      cols.forEach(([k]) => {
        const col = $('#col-' + k);
        col.innerHTML = (byStatus[k] || []).map(t => `
          <div class="bg-slate-800 rounded-lg p-2 text-xs cursor-pointer hover:bg-slate-700" onclick="window.open('/tasks?id=${t.id}','_blank')">
            <div class="font-medium">${esc(t.title)}</div>
            <div class="text-slate-500 mt-1">${t.priority || ''} · ${fmtDate(t.due_date)}</div>
          </div>`).join('') || '<div class="text-slate-600 text-xs">—</div>';
      });
    } catch (e) { c.innerHTML = '<div class="text-red-400">Ошибка</div>'; }
  };

  // ──────── QBR ────────
  function renderQbrMetricsTable(m) {
    const deltas = (m.deltas && m.deltas.per_product) || {};
    const rows = Object.entries(m.per_product || {}).map(([code, pm]) => {
      const d = deltas[code] || {};
      const metricKeys = Object.keys(pm).filter(k => typeof pm[k] === 'number');
      return metricKeys.map(k => {
        const dv = d[k];
        const dStr = dv == null ? '—' : (dv > 0 ? `+${dv}%` : `${dv}%`);
        const color = dv == null ? 'text-slate-500' : (dv > 0 ? 'text-green-400' : (dv < 0 ? 'text-red-400' : 'text-slate-400'));
        return `<tr class="border-t border-slate-700"><td class="p-2">${esc(code)}</td><td class="p-2">${esc(k)}</td><td class="p-2">${pm[k]}</td><td class="p-2 ${color}">${dStr}</td></tr>`;
      }).join('');
    }).join('');
    return `<div class="bg-slate-900 rounded-lg p-3 mb-3">
      <div class="text-xs text-slate-400 mb-2">📊 Метрики за ${esc(m.period || '—')}</div>
      <table class="w-full text-sm"><thead class="text-xs text-slate-400"><tr><th class="text-left p-2">Продукт</th><th class="text-left p-2">Метрика</th><th class="text-left p-2">Значение</th><th class="text-left p-2">Δ к прошлому</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="4" class="p-4 text-center text-slate-500">Нет числовых метрик</td></tr>'}</tbody></table>
    </div>`;
  }

  window.qbrAutoCollect = async function () {
    if (!confirm('Собрать метрики за текущий квартал из Merchrules?')) return;
    try {
      const d = await api(`/api/clients/${CID}/qbr/auto-collect`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({})});
      alert('✅ Собрано: ' + (d.quarter || ''));
      loadQBR();
    } catch (e) { alert('Ошибка автосбора'); }
  };

  window.loadQBR = async function () {
    const c = $('#tab-qbr');
    try {
      const d = await api(`/api/clients/${CID}/qbr`);
      const q = d.current_qbr;
      const metrics = (q && q.metrics) || null;
      const headerControls = `
        <div class="flex items-center gap-2 mb-3">
          <button onclick="qbrAutoCollect()" class="px-3 py-1.5 rounded-lg bg-purple-600 text-sm">🔄 Автосбор данных</button>
          <span class="text-xs text-slate-500">
            ${metrics && metrics.collected_at ? `Собрано: ${fmtDate(metrics.collected_at)} · ${Object.keys(metrics.per_product||{}).length} продуктов` : 'Данные ещё не собраны'}
          </span>
        </div>
        ${metrics && metrics.per_product ? renderQbrMetricsTable(metrics) : ''}
      `;
      if (!q) {
        c.innerHTML = `${headerControls}<div class="text-center py-6"><div class="text-slate-400 mb-3">QBR ещё не создан</div><a href="/client/${CID}/qbr" class="px-4 py-2 rounded-lg bg-indigo-600">Создать QBR</a></div>`;
        return;
      }
      c.innerHTML = `
        ${headerControls}
        <div class="space-y-4">
          <div class="flex items-center justify-between">
            <div><div class="text-xs text-slate-400">Квартал</div><div class="font-bold">${esc(q.quarter || '—')}</div></div>
            <a href="/client/${CID}/qbr" class="text-indigo-400 text-sm">Редактировать →</a>
          </div>
          <div class="grid md:grid-cols-2 gap-3">
            <div class="bg-slate-900 rounded-lg p-3"><div class="text-xs text-slate-400 mb-2">🎯 Достижения</div><ul class="text-sm space-y-1">${(q.achievements||[]).map(a=>`<li>• ${esc(a)}</li>`).join('') || '<li class="text-slate-500">—</li>'}</ul></div>
            <div class="bg-slate-900 rounded-lg p-3"><div class="text-xs text-slate-400 mb-2">⚠️ Проблемы</div><ul class="text-sm space-y-1">${(q.issues||[]).map(a=>`<li>• ${esc(a)}</li>`).join('') || '<li class="text-slate-500">—</li>'}</ul></div>
            <div class="bg-slate-900 rounded-lg p-3 md:col-span-2"><div class="text-xs text-slate-400 mb-2">🚀 Цели следующего квартала</div><ul class="text-sm space-y-1">${(q.next_goals||[]).map(a=>`<li>• ${esc(a)}</li>`).join('') || '<li class="text-slate-500">—</li>'}</ul></div>
          </div>
          ${q.summary ? `<div class="bg-slate-900 rounded-lg p-3"><div class="text-xs text-slate-400 mb-1">Summary</div><div class="text-sm">${esc(q.summary)}</div></div>` : ''}
        </div>`;
    } catch (e) { c.innerHTML = '<div class="text-red-400">Ошибка загрузки QBR</div>'; }
  };

  // ──────── MEETINGS (FullCalendar + followup sheet) ────────
  let fc = null;
  window.loadMeetings = async function () {
    const c = $('#tab-meetings');
    c.innerHTML = `
      <div id="fc-wrap" class="bg-slate-900 rounded-lg p-3"><div id="calendar-el"></div></div>
      <div id="meeting-sheet" class="hidden mt-3 bg-slate-900 border border-slate-700 rounded-lg p-4"></div>`;
    let meetings = [];
    try {
      const d = await api(`/api/clients/${CID}/meetings`).catch(() => null);
      meetings = d ? (d.meetings || d) : [];
    } catch (_) {}
    if (!meetings.length) {
      // fallback через timeline
      try {
        const t = await api(`/api/clients/${CID}/timeline?type=meeting&limit=100`);
        meetings = (t.events || []).map(e => ({id: e.id, date: e.date, title: e.title, type: 'meeting'}));
      } catch (_) {}
    }
    const now = Date.now();
    const events = meetings.map(m => {
      const date = new Date(m.date);
      const past = date.getTime() < now;
      const status = m.followup_status || (past ? 'pending' : 'upcoming');
      const color = {upcoming:'#3b82f6', pending:'#eab308', sent:'#22c55e', skipped:'#64748b', done:'#22c55e'}[status] || '#64748b';
      return {id: String(m.id), title: m.title || m.type || 'Встреча', start: m.date, backgroundColor: color, borderColor: color, extendedProps: {meeting: m, past}};
    });
    if (fc) { fc.destroy(); fc = null; }
    fc = new FullCalendar.Calendar($('#calendar-el'), {
      initialView: 'dayGridMonth', locale: 'ru', height: 500, events,
      eventClick: (info) => openMeetingSheet(info.event.extendedProps.meeting, info.event.extendedProps.past)
    });
    fc.render();
  };

  async function openMeetingSheet(meeting, past) {
    const sheet = $('#meeting-sheet');
    sheet.classList.remove('hidden');
    sheet.innerHTML = past ? renderFollowup(meeting) : renderPrep(meeting);
    sheet.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  }

  function renderPrep(m) {
    return `
      <div class="flex items-center justify-between mb-2">
        <div><div class="text-xs text-slate-400">Будущая встреча</div><div class="font-bold">${esc(m.title || 'Встреча')}</div><div class="text-xs text-slate-500">${fmtDT(m.date)}</div></div>
        <button onclick="$('#meeting-sheet').classList.add('hidden')" class="text-slate-400">✕</button>
      </div>
      <div class="space-y-2">
        <div class="text-sm">🎯 <a href="/prep/${CID}" class="text-indigo-400">Открыть полный Prep</a></div>
        <button onclick="generatePrep(${m.id})" class="px-3 py-1.5 rounded-lg bg-indigo-600 text-sm">🤖 AI-подготовка</button>
        <div id="prep-result" class="text-sm text-slate-300"></div>
      </div>`;
  }

  function renderFollowup(m) {
    const hasFup = m.followup_text && m.followup_text.length;
    const hasTranscript = m.transcript && m.transcript.length;
    const hasKtalk = !!m.ktalk_event_id;
    return `
      <div class="flex items-center justify-between mb-2">
        <div><div class="text-xs text-slate-400">Прошедшая встреча</div><div class="font-bold">${esc(m.title || 'Встреча')}</div><div class="text-xs text-slate-500">${fmtDT(m.date)} · followup: ${esc(m.followup_status || 'pending')}</div></div>
        <button onclick="$('#meeting-sheet').classList.add('hidden')" class="text-slate-400">✕</button>
      </div>

      <div class="mb-3">
        <div class="text-xs text-slate-400 mb-1">Транскрипт</div>
        <div class="flex gap-2 mb-2 flex-wrap">
          ${hasKtalk ? `<button onclick="pullKtalkTranscript(${m.id})" class="px-3 py-1.5 rounded-lg bg-indigo-600 text-sm">⬇ Получить из Ktalk</button>` : ''}
          <label class="px-3 py-1.5 rounded-lg bg-slate-700 text-sm cursor-pointer">📎 Загрузить файл<input type="file" class="hidden" accept=".txt,.vtt,.srt" onchange="loadTranscriptFile(event, ${m.id})"></label>
        </div>
        <textarea id="transcript-text" class="w-full bg-slate-800 rounded-lg p-2 text-xs" rows="4" placeholder="Или вставь транскрипт вручную...">${esc(m.transcript || '')}</textarea>
        <button onclick="processTranscript(${m.id})" class="mt-2 px-3 py-1.5 rounded-lg bg-purple-600 text-sm">🤖 Разобрать через AI</button>
      </div>

      <div class="text-xs text-slate-400 mb-1">Followup</div>
      <textarea id="fup-text" class="w-full bg-slate-800 rounded-lg p-2 text-sm" rows="6" placeholder="Текст followup (создастся автоматически из транскрипта)...">${esc(m.followup_text || '')}</textarea>
      <div class="flex gap-2 mt-2 flex-wrap">
        ${!hasFup ? `<button onclick="generateFup(${m.id})" class="px-3 py-1.5 rounded-lg bg-indigo-600 text-sm">🤖 Сгенерировать по транскрипту</button>` : ''}
        <button onclick="sendFup(${m.id})" class="px-3 py-1.5 rounded-lg bg-green-600 text-sm">✅ Отправить</button>
        <button onclick="skipFup(${m.id})" class="px-3 py-1.5 rounded-lg bg-slate-700 text-sm">Пропустить</button>
        <a href="/followup/${CID}" class="ml-auto text-indigo-400 text-sm self-center">Открыть полную</a>
      </div>`;
  }

  window.pullKtalkTranscript = async function (mid) {
    try {
      const d = await api(`/api/meetings/${mid}/transcript/pull`, {method: 'POST'});
      $('#transcript-text').value = d.transcript || '';
      alert('Транскрипт получен из Ktalk');
    } catch (e) { alert('Ошибка: не удалось получить транскрипт из Ktalk (возможно, встреча не связана с Ktalk event)'); }
  };

  window.loadTranscriptFile = function (event, mid) {
    const file = event.target.files[0]; if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => { $('#transcript-text').value = e.target.result; };
    reader.readAsText(file);
  };

  window.processTranscript = async function (mid) {
    const transcript = $('#transcript-text').value.trim();
    if (!transcript) { alert('Вставь транскрипт или загрузи файл'); return; }
    try {
      const d = await api(`/api/meetings/${mid}/transcript/process`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({transcript})});
      if (d.followup_text) $('#fup-text').value = d.followup_text;
      alert('✅ Разобрано. Action items: ' + (d.action_items_count || 0));
    } catch (e) { alert('Ошибка обработки'); }
  };

  window.generatePrep = async function (mid) {
    const out = $('#prep-result');
    out.innerHTML = '<div class="text-slate-500">Генерация brief…</div>';
    try {
      const d = await api('/api/ai/generate-prep', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({client_id: Number(CID), meeting_id: mid}),
      });
      const text = d.brief || d.text || d.prep_text || '';
      out.innerHTML = text
        ? `<div class="whitespace-pre-wrap text-sm bg-slate-800 rounded p-3 mt-2">${esc(text)}</div>
           <a href="/prep/${CID}" class="text-xs text-indigo-400">Открыть полную страницу prep →</a>`
        : '<div class="text-slate-500">AI не вернул текст</div>';
    } catch (e) { out.innerHTML = '<div class="text-red-400">Ошибка генерации</div>'; }
  };
  window.generateFup = async function (mid) {
    try {
      const d = await api(`/api/meetings/${mid}/followup/generate`, {method: 'POST'});
      $('#fup-text').value = d.followup_text || d.text || '';
    } catch (e) { alert('Ошибка генерации'); }
  };
  window.sendFup = async function (mid) {
    try {
      await api(`/api/meetings/${mid}/followup/send`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: $('#fup-text').value})});
      alert('✅ Отправлено'); loadMeetings();
    } catch (e) { alert('Ошибка'); }
  };
  window.skipFup = async function (mid) {
    try {
      await api(`/api/meetings/${mid}/followup/skip`, {method: 'POST'});
      alert('Пропущено'); loadMeetings();
    } catch (e) { alert('Ошибка'); }
  };

  // ──────── RULES ────────
  window.loadRules = async function () {
    const c = $('#tab-rules');
    c.innerHTML = '<div class="text-slate-500">Загрузка...</div>';
    try {
      const d = await api(`/api/clients/${CID}/merch-rules`);
      c.innerHTML = `
        <div class="flex items-center justify-between mb-3">
          <div class="text-sm">Правил: ${d.total || 0} · Посл. sync: ${fmtDate(d.last_sync)}</div>
          <button onclick="syncRules()" class="px-3 py-1.5 rounded-lg bg-indigo-600 text-sm">🔄 Обновить</button>
        </div>
        <table class="w-full text-sm">
          <thead class="text-xs text-slate-400"><tr><th class="text-left p-2">Название</th><th class="text-left p-2">Тип</th><th class="text-left p-2">Статус</th><th class="text-left p-2">Приор.</th></tr></thead>
          <tbody>${(d.rules||[]).map(r => `
            <tr class="border-t border-slate-700">
              <td class="p-2">${esc(r.name)}</td>
              <td class="p-2 text-slate-400">${esc(r.rule_type||'—')}</td>
              <td class="p-2"><span class="text-xs px-2 py-0.5 rounded-full bg-slate-700">${esc(r.status)}</span></td>
              <td class="p-2">${r.priority||0}</td>
            </tr>`).join('') || '<tr><td colspan="4" class="p-4 text-center text-slate-500">Правил нет</td></tr>'}
          </tbody>
        </table>`;
    } catch (e) { c.innerHTML = '<div class="text-red-400">Ошибка</div>'; }
  };
  window.syncRules = async function () {
    try {
      const d = await api(`/api/clients/${CID}/merch-rules/sync`, {method: 'POST'});
      alert(d.message || d.status);
      loadRules();
    } catch (e) { alert('Ошибка синхронизации'); }
  };

  // ──────── FEEDS ────────
  window.loadFeeds = async function () {
    const c = $('#tab-feeds');
    c.innerHTML = '<div class="text-slate-500">Загрузка...</div>';
    try {
      const d = await api(`/api/clients/${CID}/feeds`);
      const list = d.feeds || d || [];
      c.innerHTML = `
        <div class="flex items-center justify-between mb-3">
          <div class="text-sm">Фидов: ${list.length}</div>
          <button onclick="addFeed()" class="px-3 py-1.5 rounded-lg bg-indigo-600 text-sm">+ Добавить фид</button>
        </div>
        <div class="grid md:grid-cols-2 gap-3">
          ${list.map(f => `
            <div class="bg-slate-900 rounded-lg p-3">
              <div class="flex items-start gap-2">
                <div class="flex-1">
                  <div class="font-medium">${esc(f.name || f.feed_type)}</div>
                  <div class="text-xs text-slate-400">${esc(f.feed_type)} · ${esc(f.url || '—')}</div>
                </div>
                <span class="text-xs px-2 py-0.5 rounded-full ${f.status==='ok'?'bg-green-900':'bg-red-900'}">${esc(f.status)}</span>
              </div>
              <div class="mt-2 text-xs text-slate-400">SKU: ${f.sku_count||0} · Ошибок: ${f.errors_count||0}</div>
              <div class="text-xs text-slate-500">Обновлён: ${fmtDate(f.last_updated)}</div>
              <button onclick="checkFeed(${f.id})" class="mt-2 text-xs text-indigo-400">🔄 Проверить</button>
            </div>`).join('') || '<div class="text-slate-500 col-span-2">Фидов нет</div>'}
        </div>`;
    } catch (e) { c.innerHTML = '<div class="text-red-400">Ошибка</div>'; }
  };
  window.checkFeed = async function (fid) {
    try { await api(`/api/clients/${CID}/feeds/${fid}/check`, {method: 'POST'}); loadFeeds(); }
    catch (e) { alert('Ошибка'); }
  };
  window.addFeed = async function () {
    const feed_type = prompt('Тип фида (catalog/availability/price/reviews/custom):', 'catalog');
    if (!feed_type) return;
    const name = prompt('Название:', '');
    const url = prompt('URL:', '');
    try {
      await api(`/api/clients/${CID}/feeds`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({feed_type, name, url})});
      loadFeeds();
    } catch (e) { alert('Ошибка'); }
  };

  // ──────── TICKETS ────────
  window.loadTickets = async function () {
    const c = $('#tab-tickets');
    c.innerHTML = '<div class="text-slate-500 p-4">Загрузка тикетов...</div>';
    try {
      const d = await api(`/api/clients/${CID}/tickets?status=all&limit=50`);
      const tickets = d.tickets || [];
      if (!tickets.length) {
        c.innerHTML = `
          <div class="bg-slate-900 rounded-lg p-6 text-center">
            <div class="text-slate-400 mb-3">Тикетов пока нет</div>
            <button onclick="syncTickets()" class="px-3 py-1.5 rounded-lg bg-indigo-600 text-sm">🔄 Синхронизировать с Time</button>
          </div>`;
        return;
      }
      const statusBadge = (s) => {
        const map = {open:'bg-red-500/20 text-red-300',in_progress:'bg-yellow-500/20 text-yellow-300',resolved:'bg-green-500/20 text-green-300',closed:'bg-slate-600/30 text-slate-400'};
        const label = {open:'Открыт',in_progress:'В работе',resolved:'Решён',closed:'Закрыт'}[s] || s;
        return `<span class="px-2 py-0.5 rounded text-xs ${map[s]||''}">${label}</span>`;
      };
      const openCount = tickets.filter(t => t.status==='open'||t.status==='in_progress').length;
      c.innerHTML = `
        <div class="flex items-center justify-between mb-3">
          <div class="text-sm text-slate-300">Всего: ${tickets.length} · Открыто: <span class="text-red-400 font-semibold">${openCount}</span></div>
          <button onclick="syncTickets()" class="px-3 py-1.5 rounded-lg bg-slate-700 text-sm">🔄 Синхронизировать</button>
        </div>
        <div class="space-y-2">
          ${tickets.map(t => `
            <div class="bg-slate-900 border border-slate-700 rounded-lg p-3 hover:border-slate-500 cursor-pointer" onclick="openTicket(${t.id})">
              <div class="flex items-start justify-between gap-2">
                <div class="flex-1 min-w-0">
                  <div class="font-medium text-sm">${esc(t.title||'Без темы')}</div>
                  <div class="text-xs text-slate-500 mt-1 line-clamp-2">${esc((t.body||'').substring(0,200))}</div>
                </div>
                ${statusBadge(t.status)}
              </div>
              <div class="flex items-center gap-3 mt-2 text-xs text-slate-500">
                <span>👤 ${esc(t.author||'—')}</span>
                <span>🕒 ${fmtDate(t.opened_at)}</span>
                ${t.comments_count ? `<span>💬 ${t.comments_count}</span>` : ''}
                ${t.last_comment_snippet ? `<span class="truncate flex-1">· ${esc(t.last_comment_snippet.substring(0,80))}</span>` : ''}
                <a href="${esc(t.external_url||'#')}" target="_blank" class="text-indigo-400 ml-auto" onclick="event.stopPropagation()">↗ Time</a>
              </div>
            </div>
          `).join('')}
        </div>`;
    } catch (e) {
      c.innerHTML = `<div class="text-red-400 p-4">Ошибка: ${esc(e.message||'')}</div>`;
    }
  };

  window.openTicket = async function (tid) {
    try {
      const d = await api(`/api/clients/${CID}/tickets/${tid}/thread`);
      const t = d.ticket; const comments = d.comments || [];
      const html = `
        <div class="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onclick="if(event.target===this)this.remove()">
          <div class="bg-slate-900 border border-slate-700 rounded-lg max-w-2xl w-full max-h-[85vh] overflow-y-auto">
            <div class="sticky top-0 bg-slate-900 border-b border-slate-700 p-3 flex items-center justify-between">
              <div class="font-bold">${esc(t.title)}</div>
              <button onclick="this.closest('.fixed').remove()" class="text-slate-400">✕</button>
            </div>
            <div class="p-4 space-y-3">
              <div class="text-xs text-slate-500">👤 ${esc(t.author||'—')} · ${fmtDate(t.opened_at)} · <a href="${esc(t.external_url)}" target="_blank" class="text-indigo-400">Открыть в Time ↗</a></div>
              <div class="text-sm whitespace-pre-wrap bg-slate-800 rounded p-3">${esc(t.body||'')}</div>
              <div class="text-xs text-slate-400 mt-3">Комментарии (${comments.length})</div>
              ${comments.map(cm => `
                <div class="bg-slate-800 rounded p-2 text-sm">
                  <div class="text-xs text-slate-500 mb-1">${esc(cm.author||'—')} · ${fmtDate(cm.posted_at)}</div>
                  <div class="whitespace-pre-wrap">${esc(cm.body||'')}</div>
                </div>
              `).join('')}
            </div>
          </div>
        </div>`;
      const wrap = document.createElement('div'); wrap.innerHTML = html; document.body.appendChild(wrap.firstElementChild);
    } catch (e) { alert('Ошибка загрузки треда'); }
  };

  window.syncTickets = async function () {
    const btn = (typeof event !== 'undefined' && event) ? event.target : null;
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Синхронизация...'; }
    try {
      const d = await api('/api/tickets/sync', {method: 'POST'});
      alert(`✅ Новых: ${d.ingested||0}, Обновлено: ${d.updated||0}, Без привязки: ${d.unlinked||0}`);
      loadTickets();
    } catch (e) { alert('Ошибка синхронизации'); }
    finally { if (btn) { btn.disabled = false; btn.textContent = '🔄 Синхронизировать'; } }
  };

  // ──────── OVERVIEW: tickets chip ────────
  // Маленький чип «🎫 Тикеты: N» в шапке. Вставляется в #products-chips
  // или рядом как отдельная плашка, если контейнер не найден.
  async function loadTicketsChip() {
    try {
      const d = await api(`/api/clients/${CID}/tickets?status=open&limit=1`);
      const openCount = (d && (d.open_count != null ? d.open_count : (d.total != null ? d.total : (d.tickets||[]).length))) || 0;
      const chipHtml = `<span id="tickets-chip" class="text-xs px-2 py-1 rounded-full bg-red-900/40 text-red-300 cursor-pointer hover:bg-red-900/60" onclick="switchTab('tickets')" title="Открытые тикеты">🎫 Тикеты: ${openCount}</span>`;
      const chips = $('#products-chips');
      if (chips) {
        // удалим старый чип, если уже был, чтобы не дублировать при перезагрузке overview
        const old = document.getElementById('tickets-chip');
        if (old) old.remove();
        chips.insertAdjacentHTML('beforeend', chipHtml);
      } else {
        const host = $('#hdr-metrics');
        if (host && !document.getElementById('tickets-chip')) {
          host.insertAdjacentHTML('afterend', `<div class="mt-2">${chipHtml}</div>`);
        }
      }
    } catch (_) { /* тихо — эндпоинт может быть ещё не реализован */ }
  }

  // Пытаемся вставить чип после первичной загрузки overview.
  // loadOverview() вызывается в inline-скрипте client_detail.html на init.
  // Дадим ей 300 мс, потом добавим чип; плюс повторная попытка через 2 сек на случай медленной сети.
  setTimeout(loadTicketsChip, 300);
  setTimeout(loadTicketsChip, 2000);
})();
