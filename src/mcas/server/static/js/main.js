import { state, storageGet } from './state.js';
import { api } from './api.js';
import { $, el } from './utils/dom.js';
import { groupKey } from './utils/format.js';
import { withBusy } from './utils/busy.js';
import { initTheme, toggleTheme } from './components/theme.js';
import { toast } from './components/toast.js';
import { askConfirm } from './components/confirmModal.js';
import { renderStatus, switchTab } from './render/status.js';
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
import { closePreview, initDrawer } from './drawer.js';
import { initTrimModal } from './modals/trimModal.js';
import { initCleanupModal } from './modals/cleanupModal.js';
import { initSyncDirModal } from './modals/syncDirModal.js';
import { initAutosyncPanel } from './panels/autosync.js';

// Theme init
initTheme();

// Topbar & navigation events
$('themeBtn').onclick = toggleTheme;
$('tabBtnSessions').onclick = () => switchTab('sessions');
$('tabBtnAutosync').onclick = () => switchTab('autosync');
$('autosyncPill').onclick = () => switchTab('autosync');

$('refreshBtn').onclick = () => refreshAll(true);
$('reconnectBtn').onclick = () => refreshAll(true);

$('autostartToggle').onchange = async (e) => {
  const want = e.target.checked;
  await withBusy(async () => {
    try {
      const r = await api('autostart', { on: want });
      toast(r.message || (want ? 'Start with Windows enabled.' : 'Start with Windows disabled.'), 'ok');
      if (r.status) renderStatus(r.status);
      else if (typeof r.autostart === 'boolean') $('autostartToggle').checked = r.autostart;
    } catch (err) {
      $('autostartToggle').checked = !want;
      toast(err.message, 'err');
    }
  });
};

$('quitBtn').onclick = async () => {
  const ok = await askConfirm(
    'Stop mcas server',
    'Stop the mcas web UI server, background Auto-Sync, and tray icon? Run "mcas ui" again to reopen.',
    'Stop server',
    true
  );
  if (!ok) return;
  state.stopped = true;
  stopPolling();
  try { await api('quit', {}); } catch {}
  document.body.replaceChildren(
    el('main', { className: 'app' },
      el('div', { className: 'card', style: { padding: '24px' } },
        el('h2', { textContent: 'mcas server stopped', style: { marginTop: '0' } }),
        el('p', { className: 'brand-sub', textContent: 'The web server and tray icon have shut down. You can close this tab or run "mcas ui" to start again.' })
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
    $('openSyncDirLabel').textContent = state.cwdFilter ? 'Sync this folder \u2192 ...' : 'Sync folder \u2192 ...';
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
$('bulkCopyBtn').onclick = copySelectedSessions;
$('bulkDeleteBtn').onclick = confirmDeleteSelected;

// Modals, Drawer & Auto-Sync Panel
initTrimModal();
initCleanupModal();
initSyncDirModal();
initDrawer();
initAutosyncPanel();

// Global click / keyboard dismissal
document.addEventListener('click', (e) => {
  if (state.openDropdownKey && !e.target.closest('[data-dropdown-host="true"]')) {
    closeProviderDropdown(true);
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (!$('confirmModal').hidden) return;
    if (state.openDropdownKey) { closeProviderDropdown(true); return; }
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
