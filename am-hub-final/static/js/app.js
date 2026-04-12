document.addEventListener('DOMContentLoaded', () => {
    // Toggle Command Palette
    window.toggleCmdPalette = () => {
        const p = document.getElementById('cmd-palette');
        p.classList.toggle('hidden');
        if (!p.classList.contains('hidden')) document.getElementById('cmd-input').focus();
    };
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); toggleCmdPalette(); }
        if (e.key === 'Escape') document.getElementById('cmd-palette').classList.add('hidden');
    });
    document.querySelector('.cmd-overlay').addEventListener('click', toggleCmdPalette);

    // LOAD DATA FROM API
    fetch('/api/clients')
        .then(r => r.json())
        .then(data => {
            const list = document.getElementById('clients-list');
            const nameEl = document.getElementById('client-name');
            const scoreEl = document.getElementById('health-score');
            
            if (data && data.length > 0) {
                // Update Hero Card with first client
                nameEl.textContent = data[0].name;
                scoreEl.textContent = data[0].health_score || 'N/A';
                
                // Render List
                list.innerHTML = data.map(c => 
                    \<li class="task-item"><strong>\</strong> <span>(\)</span></li>\
                ).join('');
                
                document.getElementById('ai-message').textContent = "Data loaded successfully!";
            } else {
                list.innerHTML = '<li>No clients found in DB. Run Airtable sync.</li>';
                nameEl.textContent = "No Data";
            }
        })
        .catch(err => {
            console.error(err);
            document.getElementById('clients-list').innerHTML = '<li>Error loading data</li>';
        });
});
