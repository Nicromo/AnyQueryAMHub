document.addEventListener('DOMContentLoaded', () => {
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
});