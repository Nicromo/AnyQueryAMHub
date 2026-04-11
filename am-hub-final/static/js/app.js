document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        const p = document.getElementById('cmd-palette');
        p.classList.toggle('hidden');
    }
});
document.querySelector('.cmd-overlay').addEventListener('click', () => {
    document.getElementById('cmd-palette').classList.add('hidden');
});
