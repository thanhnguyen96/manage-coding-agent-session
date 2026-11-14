import { storageGet, storageSet } from '../state.js';
import { $, svgIcon } from '../utils/dom.js';
import { t } from '../i18n.js';

export function initTheme() {
  const savedTheme = storageGet('mcas.theme');
  if (savedTheme === 'light' || savedTheme === 'dark') {
    document.documentElement.dataset.theme = savedTheme;
  }
  updateThemeButton();
  if (window.matchMedia) {
    try {
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', updateThemeButton);
    } catch {}
  }
}

export function isDarkEffective() {
  const pref = document.documentElement.dataset.theme;
  if (pref === 'dark') return true;
  if (pref === 'light') return false;
  return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
}

export function updateThemeButton() {
  const dark = isDarkEffective();
  const btn = $('themeBtn');
  btn.replaceChildren(svgIcon(dark
    ? ['M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z']
    : ['M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z', 'M12 2v2', 'M12 20v2', 'm4.93 4.93 1.41 1.41', 'm17.66 17.66 1.41 1.41', 'M2 12h2', 'M20 12h2', 'm6.34 17.66-1.41 1.41', 'm19.07 4.93-1.41 1.41'],
  15));
  btn.title = dark ? t('top.themeToLight') : t('top.themeToDark');
  btn.setAttribute('aria-label', t('top.themeAria'));
}

export function toggleTheme() {
  const next = isDarkEffective() ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  storageSet('mcas.theme', next);
  updateThemeButton();
}
