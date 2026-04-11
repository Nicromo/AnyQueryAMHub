document.addEventListener('DOMContentLoaded', () => {
    // Command Palette Toggle
    window.toggleCmdPalette = () => {
        const palette = document.getElementById('cmd-palette');
        palette.classList.toggle('hidden');
        if (!palette.classList.contains('hidden')) {
            document.getElementById('cmd-input').focus();
        }
    };

    // Keyboard Shortcuts
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            toggleCmdPalette();
        }
        if (e.key === 'Escape') {
            document.getElementById('cmd-palette').classList.add('hidden');
        }
        if (e.key === 't' || e.key === 'T') {
            document.body.classList.toggle('light-mode');
        }
    });

    // Close on overlay click
    document.querySelector('.cmd-overlay').addEventListener('click', toggleCmdPalette);
});
