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
    $('#prep-result').innerHTML = '<div class="text-slate-500">Генерация...</div>';
    try {
      // TODO: реальный эндпоинт для prep — пока через существующий
      window.location.href = `/prep/${CID}`;
    } catch (e) { $('#prep-result').innerHTML = '<div class="text-red-400">Ошибка</div>'; }
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
})();
