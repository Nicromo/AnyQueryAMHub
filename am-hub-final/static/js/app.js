document.addEventListener('DOMContentLoaded', () => {
    loadData();
    setupKeyboard();
});

async function loadData() {
    // Загрузка статистики
    try {
        const statsRes = await fetch('/api/stats');
        const stats = await statsRes.json();
        document.getElementById('stat-clients').innerText = stats.total_clients;
        document.getElementById('stat-critical').innerText = stats.critical_health;
        document.getElementById('stat-tasks').innerText = stats.total_tasks;
    } catch (e) { console.error('Stats error', e); }

    // Загрузка клиентов (для таблицы и героя)
    try {
        const clientsRes = await fetch('/api/clients');
        const clients = await clientsRes.json();
        
        // Заполняем таблицу
        const tbody = document.getElementById('clients-table-body');
        tbody.innerHTML = '';
        clients.forEach(c => {
            const healthClass = c.health_score < 50 ? 'low' : 'high';
            const trendIcon = c.revenue_trend === 'growth' ? '📈' : (c.revenue_trend === 'drop' ? '📉' : '➖');
            tbody.innerHTML += \
                <tr>
                    <td><strong>\</strong><br><small>\</small></td>
                    <td>\</td>
                    <td><span class="health-badge \">\</span></td>
                    <td>\</td>
                    <td>\ \</td>
                </tr>
            \;
        });

        // Заполняем Hero (берем первого клиента с низким здоровьем или первого в списке)
        const focusClient = clients.find(c => c.health_score < 60) || clients[0];
        if (focusClient) {
            const heroDiv = document.getElementById('hero-content');
            const isCritical = focusClient.health_score < 50;
            heroDiv.innerHTML = \
                <div class="client-info">
                    <div class="client-avatar">\</div>
                    <div><h2>\</h2><p>\</p></div>
                </div>
                <div class="health-score \">
                    <div class="score-value">\</div>
                    <div class="score-label">Health Score \</div>
                </div>
                <div class="quick-stats">
                    <div class="stat"><span class="label">Revenue</span><span class="value \">\</span></div>
                    <div class="stat"><span class="label">Tickets</span><span class="value \">\ Open</span></div>
                </div>
            \;
        }
    } catch (e) { console.error('Clients error', e); }

    // Загрузка задач
    try {
        const tasksRes = await fetch('/api/tasks');
        const tasks = await tasksRes.json();
        const taskList = document.getElementById('task-list');
        taskList.innerHTML = '';
        if (tasks.length === 0) {
            taskList.innerHTML = '<li class="task-item" style="justify-content:center; color:#94a3b8">No active tasks 🎉</li>';
        } else {
            tasks.forEach(t => {
                const badgeClass = t.priority === 'high' ? 'high' : 'medium';
                taskList.innerHTML += \
                    <li class="task-item">
                        <input type="checkbox" \>
                        <span>\</span>
                        <span class="badge \">\</span>
                    </li>
                \;
            });
        }
    } catch (e) { console.error('Tasks error', e); }
}

function setupKeyboard() {
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            toggleCmdPalette();
        }
        if (e.key === 'Escape') document.getElementById('cmd-palette').classList.add('hidden');
    });
    document.querySelector('.cmd-overlay')?.addEventListener('click', toggleCmdPalette);
}

function toggleCmdPalette() {
    const p = document.getElementById('cmd-palette');
    p.classList.toggle('hidden');
    if (!p.classList.contains('hidden')) document.getElementById('cmd-input').focus();
}

function refreshData() {
    const btn = document.querySelector('.dock-item[onclick="refreshData()"] i');
    btn.classList.add('animate-spin'); // Нужен CSS для spin или просто визуальный эффект
    loadData();
    setTimeout(() => btn.classList.remove('animate-spin'), 1000);
}

function addDemoTask() {
    const titles = ["Check ROAS anomaly", "Call client CEO", "Review QBR deck"];
    const randomTitle = titles[Math.floor(Math.random() * titles.length)] + " " + Math.floor(Math.random()*100);
    fetch('/api/tasks?title=' + encodeURIComponent(randomTitle) + '&priority=high', { method: 'POST' })
        .then(() => loadData());
}
