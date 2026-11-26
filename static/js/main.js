document.addEventListener('DOMContentLoaded', () => {
  if (window.lucide) {
    window.lucide.createIcons();
  }

  const menuBtn = document.getElementById('menu-toggle');
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebar-backdrop');

  if (menuBtn && sidebar && backdrop) {
    const toggle = (force) => {
      const open = typeof force === 'boolean' ? force : !sidebar.classList.contains('open');
      sidebar.classList.toggle('open', open);
      backdrop.classList.toggle('open', open);
    };
    menuBtn.addEventListener('click', () => toggle());
    backdrop.addEventListener('click', () => toggle(false));
  }

  document.addEventListener('keydown', (event) => {
    const isCtrlK = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k';
    if (!isCtrlK) {
      return;
    }
    event.preventDefault();
    const input = document.getElementById('top-search-input') || document.getElementById('hero-search-input');
    if (input) {
      input.focus();
      input.select();
    }
  });
});
