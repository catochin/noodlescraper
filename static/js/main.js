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

  // Hero source tabs sync with hidden input + header select
  const heroTabs = document.querySelectorAll('.source-tab[data-source]');
  const heroSourceInput = document.getElementById('hero-source-input');
  const topSourceSelect = document.getElementById('top-source-select');

  if (heroTabs.length && heroSourceInput) {
    heroTabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        const val = tab.dataset.source;
        heroSourceInput.value = val;
        if (topSourceSelect) topSourceSelect.value = val;
        heroTabs.forEach((t) => t.classList.toggle('active', t === tab));
      });
    });
  }

  // Clickable timecodes & quality switch for HTML5 player (Studentki & Porno365)
  const videoEl = document.getElementById('html5-player');
  if (videoEl) {
    document.querySelectorAll('.timecode-btn[data-time]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const sec = parseFloat(btn.dataset.time || '0');
        if (!Number.isNaN(sec)) {
          videoEl.currentTime = sec;
          videoEl.play().catch(() => {});
          videoEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      });
    });

    const qualityBtns = document.querySelectorAll('.quality-switch[data-src]');
    qualityBtns.forEach((btn) => {
      btn.addEventListener('click', () => {
        const nextSrc = btn.dataset.src;
        if (!nextSrc) return;
        const prevTime = videoEl.currentTime || 0;
        const wasPaused = videoEl.paused;
        videoEl.src = nextSrc;
        videoEl.load();
        videoEl.addEventListener(
          'loadedmetadata',
          () => {
            if (prevTime > 0) videoEl.currentTime = prevTime;
            if (!wasPaused) videoEl.play().catch(() => {});
          },
          { once: true }
        );
        qualityBtns.forEach((b) => b.classList.toggle('active', b === btn));
      });
    });
  }

  // Mirror refresh button on /mirrors
  const refreshBtn = document.getElementById('refresh-mirrors-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      refreshBtn.disabled = true;
      const original = refreshBtn.innerHTML;
      refreshBtn.textContent = 'Scanning Bing & pinging...';
      try {
        await fetch('/api/mirrors/refresh', { method: 'POST' });
        window.location.reload();
      } catch (e) {
        refreshBtn.innerHTML = original;
        refreshBtn.disabled = false;
      }
    });
  }

  // Ctrl+K search focus shortcut
  document.addEventListener('keydown', (event) => {
    const isCtrlK = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k';
    if (!isCtrlK) return;
    event.preventDefault();
    const input = document.getElementById('top-search-input') || document.getElementById('hero-search-input');
    if (input) {
      input.focus();
      input.select();
    }
  });
});
