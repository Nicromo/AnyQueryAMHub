async function loadData() {
    // Stats
    try {
        const statsRes = await fetch('/api/stats');
        const stats = await statsRes.json();
        document.getElementById('stat-clients').innerText = stats.clients || 0;
        document.getElementById('stat-tasks').innerText = stats.tasks || 0;
    } catch(e) { console.error('Stats error', e); }

    // Hero Client (First one)
    try {
        const clientsRes = await fetch('/api/clients');
        const clients = await clientsRes.json();
        const heroDiv = document.getElementById('hero-content');
        if (clients.length > 0) {
            const c = clients[0];
            const healthClass = c.health < 60 ? 'critical' : 'good';
            const healthText = c.health < 60 ? 'At Risk' : 'Stable';
            heroDiv.innerHTML = \
                <div class="client-big">
                    <div class="avatar-lg">\</div>
                    <div>
                        <h2>\</h2>
                        <p style="color:#94a3b8">\ Segment</p>
                    </div>
                </div>
                <div class="health-badge \">\ (\)</div>
                <div style="margin-top:1rem; font-size:0.9rem; color:#94a3b8">Last Checkup: \</div>
            \;
        } else {
            heroDiv.innerHTML = '<p>No clients loaded. Check Airtable sync.</p>';
        }
    } catch(e) { console.error('Clients error', e); }

    // Tasks
    try {
        const tasksRes = await fetch('/api/tasks');
        const tasks = await tasksRes.json();
        const list = document.getElementById('task-list');
        list.innerHTML = '';
        if (tasks.length === 0) list.innerHTML = '<li style="padding:1rem; color:#94a3b8">No active tasks</li>';
        tasks.forEach(t => {
            const pClass = t.priority === 'high' ? 'high' : 'medium';
            list.innerHTML += \<li class="task-item"><input type="checkbox"> <span>\</span> <span class="badge \">\</span></li>\;
        });
    } catch(e) { console.error('Tasks error', e); }

    // AI Insight Mock
    document.getElementById('ai-text').innerText = "Based on recent data, 2 clients require immediate attention due to low health scores. Suggest scheduling checkups.";
}

function toggleCmdPalette() {
    const p = document.getElementById('cmd-palette');
    p.classList.toggle('hidden');
    if (!p.classList.contains('hidden')) document.getElementById('cmd-input').focus();
}

async function createRandomTask() {
    await fetch('/api/tasks', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            title: "New Task " + Math.floor(Math.random()*100),
            priority: Math.random() > 0.5 ? 'high' : 'medium',
            status: 'todo'
        })
    });
    loadData();
}

document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); toggleCmdPalette(); }
    if (e.key === 'Escape') document.getElementById('cmd-palette').classList.add('hidden');
});
document.querySelector('.cmd-overlay').addEventListener('click', toggleCmdPalette);