(() => {
  const key = 'shipbytes-theme';
  const choices = ['auto', 'dark', 'light'];
  let current = 'auto';
  try {
    const saved = localStorage.getItem(key);
    if (choices.includes(saved)) current = saved;
  } catch (_) { /* System theme still works when storage is unavailable. */ }

  function apply(theme) {
    current = theme;
    if (theme === 'auto') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === theme));
    });
  }
  apply(current);
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.theme-switch').forEach(control => { control.hidden = false; });
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.addEventListener('click', () => {
        apply(button.dataset.themeChoice);
        try { localStorage.setItem(key, current); } catch (_) { /* Session-only choice. */ }
      });
    });
    apply(current);
  });
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) {
      apply(choices.includes(event.newValue) ? event.newValue : 'auto');
    }
  });
})();
