window.toggleCmdPalette = () => {
    const p = document.getElementById('cmd-palette');
    p.classList.toggle('hidden');
    if (!p.classList.contains('hidden')) setTimeout(() => document.getElementById('cmd-input').focus(), 100);
};
document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); toggleCmdPalette(); }
    if (e.key === 'Escape') document.getElementById('cmd-palette').classList.add('hidden');
});
document.addEventListener('DOMContentLoaded', () => {
    const overlay = document.querySelector('.cmd-overlay');
    if(overlay) overlay.addEventListener('click', toggleCmdPalette);
    if(window.lucide) lucide.createIcons();
});