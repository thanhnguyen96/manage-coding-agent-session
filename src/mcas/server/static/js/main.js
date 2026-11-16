import { state, storageGet, storageSet } from './state.js';
import { api } from './api.js';
import { $, el } from './utils/dom.js';
import { groupKey } from './utils/format.js';
import { withBusy } from './utils/busy.js';
import { initTheme, toggleTheme, updateThemeButton } from './components/theme.js';
import { toast } from './components/toast.js';
import { askConfirm } from './components/confirmModal.js';
import { renderStatus, renderWorkspaceSelect, switchTab, updateSyncDirButton } from './render/status.js';
import {
  clearCwdFilter,
  renderActiveFilters,
  renderSessions,
  setCwdFilter,
} from './render/sessionList.js';
import { closeProviderDropdown } from './providerDropdown.js';
import {
  confirmDeleteSelected,
  copySelectedSessions,
  fetchSessions,
  refreshAll,
  startPolling,
  stopPolling,
} from './actions.js';
import { closePreview, initDrawer, renderDrawerContent } from './drawer.js';
import { initTrimModal, renderTrimModalLabel } from './modals/trimModal.js';
import { initCleanupModal, renderStorageSummaryGrid } from './modals/cleanupModal.js';
import {
  initSyncDirModal,
  renderSyncDirWorkspaceOptions,
  updateSyncDirHint,
} from './modals/syncDirModal.js';
import {
  closeHelpModal,
  initHelpModal,
  openHelpModal,
  renderHelpModal,
} from './modals/helpModal.js';
import { initAutosyncPanel, renderAutosyncBasicSummary, syncAutosyncActionButtons } from './panels/autosync.js';
import { initI18n, onLangChange, setLang, t } from './i18n.js';

function normalizeUiMode(mode) {
  return mode === 'advanced' ? 'advanced' : 'basic';
}

function applyUiMode(mode, { persist = true, refreshUi = true } = {}) {
  const next = normalizeUiMode(mode);
  state.uiMode = next;
  document.documentElement.dataset.uiMode = next;
  if (persist) storageSet('mcas.uiMode', next);
  if (next === 'basic' && state.cwdCustomMode) {
    state.cwdCustomMode = false;
    $('cwdCustomWrap').hidden = true;
  }
  document.querySelectorAll('#uiModeSwitch button[data-ui-mode]').forEach((btn) => {
    const active = btn.getAttribute('data-ui-mode') === next;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
  });
  if (refreshUi) {
    updateSyncDirButton();
    renderWorkspaceSelect(state.status ? state.status.workspaces : []);
    renderActiveFilters();
    renderAutosyncBasicSummary();
  }
}

// Theme, UI mode & i18n init
initTheme();
initI18n();
updateThemeButton();
applyUiMode(storageGet('mcas.uiMode', 'basic'), { persist: false, refreshUi: false });

// Settings & Language dropdowns
const closeSettingsMenu = () => {
  const menu = $('settingsMenu');
  const btn = $('settingsBtn');
  if (menu) menu.hidden = true;
  if (btn) btn.setAttribute('aria-expanded', 'false');
};

const closeLangMenu = () => {
  const menu = $('langMenu');
  const btn = $('langBtn');
  if (menu) menu.hidden = true;
  if (btn) btn.setAttribute('aria-expanded', 'false');
};

$('settingsBtn').onclick = (e) => {
  e.stopPropagation();
  const menu = $('settingsMenu');
  const willOpen = menu.hidden;
  closeLangMenu();
  closeProviderDropdown(true);
  menu.hidden = !willOpen;
  $('settingsBtn').setAttribute('aria-expanded', String(willOpen));
};

$('langBtn').onclick = (e) => {
  e.stopPropagation();
  const menu = $('langMenu');
  const willOpen = menu.hidden;
  closeSettingsMenu();
  closeProviderDropdown(true);
  menu.hidden = !willOpen;
  $('langBtn').setAttribute('aria-expanded', String(willOpen));
};

document.querySelectorAll('#langMenu .lang-opt').forEach((opt) => {
  opt.onclick = (e) => {
    e.stopPropagation();
    const lang = opt.getAttribute('data-lang');
    closeLangMenu();
    setLang(lang);
  };
});

onLangChange(() => {
  updateThemeButton();
  if (state.status) renderStatus(state.status);
  else updateSyncDirButton();
  renderSessions();
  if (!$('previewBackdrop').hidden && state.drawerSession) renderDrawerContent(state.drawerSession);
  if (!$('trimModal').hidden) renderTrimModalLabel();
  if (!$('cleanupModal').hidden) renderStorageSummaryGrid();
  if (!$('syncDirModal').hidden) {
    renderSyncDirWorkspaceOptions();
    updateSyncDirHint();
  }
  if (!$('helpModal').hidden) renderHelpModal();
  renderAutosyncBasicSummary();
  syncAutosyncActionButtons();
});

// Topbar & navigation events
$('themeBtn').onclick = toggleTheme;
$('tabBtnSessions').onclick = () => switchTab('sessions');
$('tabBtnAutosync').onclick = () => switchTab('autosync');
$('autosyncPill').onclick = () => switchTab('autosync');
document.querySelectorAll('#uiModeSwitch button[data-ui-mode]').forEach((btn) => {
  btn.onclick = () => applyUiMode(btn.getAttribute('data-ui-mode'));
});
if ($('asEditAdvBtn')) $('asEditAdvBtn').onclick = () => applyUiMode('advanced');

$('refreshBtn').onclick = () => refreshAll(true);
$('reconnectBtn').onclick = () => refreshAll(true);

$('autostartToggle').onchange = async (e) => {
  const want = e.target.checked;
  await withBusy(async () => {
    try {
      const r = await api('autostart', { on: want });
      toast(r.message || (want ? t('top.autostartEnabled') : t('top.autostartDisabled')), 'ok');
      if (r.status) renderStatus(r.status);
      else if (typeof r.autostart === 'boolean') $('autostartToggle').checked = r.autostart;
    } catch (err) {
      $('autostartToggle').checked = !want;
      toast(err.message, 'err');
    }
  });
};

$('quitBtn').onclick = async () => {
  closeSettingsMenu();
  const ok = await askConfirm(
    t('quit.title'),
    t('quit.msg'),
    t('top.stopServer'),
    true
  );
  if (!ok) return;
  state.stopped = true;
  stopPolling();
  try { await api('quit', {}); } catch {}
  document.body.replaceChildren(
    el('main', { className: 'app' },
      el('div', { className: 'card', style: { padding: '24px' } },
        el('h2', { textContent: t('quit.stoppedTitle'), style: { marginTop: '0' } }),
        el('p', { className: 'brand-sub', textContent: t('quit.stoppedDesc') })
      )
    )
  );
};

// Filters
let cwdCustomTimer = null;
const onCwdSelectPicked = () => {
  const val = $('cwdSelect').value;
  if (val === '__custom__') {
    state.cwdCustomMode = true;
    $('cwdCustomWrap').hidden = false;
    $('cwdCustomInput').value = state.cwdFilter;
    $('cwdCustomInput').focus();
  } else {
    if (val === state.cwdFilter && !state.cwdCustomMode) return;
    setCwdFilter(val);
  }
};
$('cwdSelect').onchange = onCwdSelectPicked;
$('cwdSelect').oninput = onCwdSelectPicked;
$('cwdCustomApply').onclick = () => {
  clearTimeout(cwdCustomTimer);
  setCwdFilter($('cwdCustomInput').value);
};
$('cwdCustomInput').oninput = (e) => {
  const v = e.target.value.trim();
  clearTimeout(cwdCustomTimer);
  cwdCustomTimer = setTimeout(() => {
    if (!state.cwdCustomMode) return;
    if (v === state.cwdFilter) return;
    state.cwdFilter = v;
    state.limit = 200;
    updateSyncDirButton();
    renderActiveFilters();
    fetchSessions({ silent: false });
  }, 350);
};
$('cwdCustomInput').onkeydown = (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    clearTimeout(cwdCustomTimer);
    setCwdFilter($('cwdCustomInput').value);
  }
};
$('cwdCustomClear').onclick = () => {
  clearTimeout(cwdCustomTimer);
  clearCwdFilter();
};

let searchTimer = null;
$('searchInput').oninput = (e) => {
  const v = e.target.value;
  $('searchClear').hidden = !v;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.q = v.trim();
    fetchSessions();
  }, 200);
};
$('searchClear').onclick = () => {
  $('searchInput').value = '';
  $('searchClear').hidden = true;
  state.q = '';
  fetchSessions();
};
$('includeAll').onchange = (e) => {
  state.includeAll = e.target.checked;
  fetchSessions();
};
$('sortSelect').onchange = (e) => {
  state.sort = e.target.value || 'updated_desc';
  fetchSessions();
};
$('loadMoreBtn').onclick = () => {
  state.limit += 200;
  fetchSessions();
};

// Selection
$('selectAll').onchange = (e) => {
  for (const g of state.groups) {
    if (e.target.checked) state.selected.set(groupKey(g), g);
    else state.selected.delete(groupKey(g));
  }
  renderSessions();
};
$('clearSelBtn').onclick = () => {
  state.selected.clear();
  renderSessions();
};
$('bulkOptionsBtn').onclick = () => {
  const wrap = $('bulkOptionsWrap');
  const willShow = wrap.hidden;
  wrap.hidden = !willShow;
  $('bulkOptionsBtn').setAttribute('aria-expanded', String(willShow));
};
$('bulkCopyBtn').onclick = copySelectedSessions;
$('bulkDeleteBtn').onclick = confirmDeleteSelected;

// Modals, Drawer & Auto-Sync Panel
initTrimModal();
initCleanupModal();
initSyncDirModal();
initHelpModal();
initDrawer();
initAutosyncPanel();
updateSyncDirButton();

// Global click / keyboard dismissal
document.addEventListener('click', (e) => {
  if (state.openDropdownKey && !e.target.closest('[data-dropdown-host="true"]')) {
    closeProviderDropdown(true);
  }
  const settingsMenu = $('settingsMenu');
  if (settingsMenu && !settingsMenu.hidden && !e.target.closest('#settingsWrap')) {
    closeSettingsMenu();
  }
  const langMenu = $('langMenu');
  if (langMenu && !langMenu.hidden && !e.target.closest('#langWrap')) {
    closeLangMenu();
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    const tgt = e.target;
    const tag = tgt && tgt.tagName ? tgt.tagName.toLowerCase() : '';
    const isTyping = tag === 'input' || tag === 'textarea' || tag === 'select' || !!(tgt && tgt.isContentEditable);
    if (!isTyping && $('confirmModal').hidden) {
      e.preventDefault();
      closeSettingsMenu();
      closeLangMenu();
      openHelpModal();
      return;
    }
  }
  if (e.key === 'Escape') {
    const settingsMenu = $('settingsMenu');
    if (settingsMenu && !settingsMenu.hidden) { closeSettingsMenu(); return; }
    const langMenu = $('langMenu');
    if (langMenu && !langMenu.hidden) { closeLangMenu(); return; }
    if (!$('confirmModal').hidden) return;
    if (state.openDropdownKey) { closeProviderDropdown(true); return; }
    if (!$('helpModal').hidden) { closeHelpModal(); return; }
    if (!$('trimModal').hidden) { $('trimModal').hidden = true; return; }
    if (!$('cleanupModal').hidden) { $('cleanupModal').hidden = true; return; }
    if (!$('syncDirModal').hidden) { $('syncDirModal').hidden = true; return; }
    if (!$('previewBackdrop').hidden) closePreview();
  }
});

// Restore active tab & start polling
const savedTab = storageGet('mcas.tab', 'sessions');
if (savedTab === 'autosync') switchTab('autosync');

startPolling();
refreshAll(true);
