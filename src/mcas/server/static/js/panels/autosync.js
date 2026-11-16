import { state } from '../state.js';
import { api } from '../api.js';
import { $, el } from '../utils/dom.js';
import { withBusy } from '../utils/busy.js';
import {
  formatLocalDate,
  formatRelative,
  formatResumeCmd,
  normalizePathKey,
  providerBadgeClass,
} from '../utils/format.js';
import { copyText, toast } from '../components/toast.js';
import { renderStatus } from '../render/status.js';
import { fetchSessions, fetchStatus } from '../actions.js';
import { t } from '../i18n.js';

export function renderAutosyncBasicSummary() {
  const box = $('asBasicSummary');
  const textEl = $('asBasicSummaryText');
  if (!box || !textEl) return;
  const scope = $('asScope')?.value || 'all';
  const interval = Number($('asInterval')?.value) || 30;
  const includeThinking = !!$('asIncludeThinking')?.checked;
  const parts = [];
  if (scope === 'watched') {
    const n = state.watchedDraft.length;
    parts.push(t(n === 1 ? 'as.sumWatchedOne' : 'as.sumWatchedMany', { n }));
  }
  if (interval !== 30) {
    parts.push(t('as.sumInterval', { s: interval }));
  }
  if (includeThinking) {
    parts.push(t('as.sumThinking'));
  }
  if (!parts.length) {
    box.hidden = true;
    textEl.textContent = '';
    return;
  }
  textEl.textContent = t('as.basicSummary', { parts: parts.join(' \u00b7 ') });
  box.hidden = false;
}

export function syncAutosyncActionButtons() {
  const saveBtn = $('asSaveBtn');
  if (saveBtn) saveBtn.disabled = !state.autosyncDirty;
  const syncBtn = $('asSyncNowBtn');
  if (syncBtn) {
    syncBtn.textContent = state.autosyncDirty ? t('as.saveAndSyncBtn') : t('as.syncNowBtn');
  }
}

export function markAutosyncDirty() {
  state.autosyncDirty = true;
  $('asDirtyPill').hidden = false;
  syncAutosyncActionButtons();
}

export function clearAutosyncDirty() {
  state.autosyncDirty = false;
  $('asDirtyPill').hidden = true;
  syncAutosyncActionButtons();
}

export function collectAutosyncPayload(runNow = false) {
  const payload = {
    enabled: $('asEnabled').checked,
    direction: $('asDirection').value,
    intervalSec: Number($('asInterval').value) || 30,
    scope: $('asScope').value,
    watchedFolders: [...state.watchedDraft],
    includeThinking: $('asIncludeThinking').checked,
  };
  if (runNow) payload.runNow = true;
  return payload;
}

export function renderWatchedFolders(workspaces) {
  const scope = $('asScope').value;
  $('watchedTitle').textContent = scope === 'watched'
    ? t('as.watchedActive')
    : t('as.watchedInactive');
  $('watchedBox').style.opacity = scope === 'watched' ? '1' : '.72';

  const host = $('watchedList');
  host.replaceChildren();
  const known = new Map();
  for (const w of (workspaces || [])) {
    if (w && w.cwd) known.set(normalizePathKey(w.cwd), w);
  }
  const allPaths = [];
  const seen = new Set();
  for (const w of (workspaces || [])) {
    if (w && w.cwd && !seen.has(normalizePathKey(w.cwd))) {
      seen.add(normalizePathKey(w.cwd));
      allPaths.push(w.cwd);
    }
  }
  for (const p of state.watchedDraft) {
    if (p && !seen.has(normalizePathKey(p))) {
      seen.add(normalizePathKey(p));
      allPaths.push(p);
    }
  }

  if (!allPaths.length) {
    host.append(el('div', { className: 'brand-sub', textContent: t('as.noWorkspaces') }));
    return;
  }

  for (const folder of allPaths) {
    const key = normalizePathKey(folder);
    const checked = state.watchedDraft.some((p) => normalizePathKey(p) === key);
    const ws = known.get(key);
    const cb = el('input', {
      type: 'checkbox',
      checked,
      onchange: () => {
        if (cb.checked) {
          if (!state.watchedDraft.some((p) => normalizePathKey(p) === key)) state.watchedDraft.push(folder);
        } else {
          state.watchedDraft = state.watchedDraft.filter((p) => normalizePathKey(p) !== key);
        }
        markAutosyncDirty();
      },
    });
    const label = el('label', {},
      cb,
      el('span', { className: 'watched-path mono', textContent: folder, title: folder })
    );
    const row = el('div', { className: 'watched-item' }, label);
    if (ws) {
      const cnt = ws.total ?? 0;
      row.append(el('span', {
        className: 'brand-sub',
        textContent: t(cnt === 1 ? 'as.sessionsCountOne' : 'as.sessionsCountMany', { n: cnt }),
      }));
    } else {
      row.append(el('button', {
        type: 'button',
        className: 'sm ghost',
        textContent: t('as.removeFolder'),
        onclick: () => {
          state.watchedDraft = state.watchedDraft.filter((p) => normalizePathKey(p) !== key);
          markAutosyncDirty();
          renderWatchedFolders(state.status ? state.status.workspaces : []);
        },
      }));
    }
    host.append(row);
  }
}

export function renderHistory(history) {
  // Backend returns newest-first (capped at 100)
  const list = Array.isArray(history) ? history : [];
  $('historyCount').textContent = list.length === 1
    ? t('as.entriesOne', { n: list.length })
    : t('as.entriesMany', { n: list.length });
  const tbody = $('historyBody');
  tbody.replaceChildren();
  if (!list.length) {
    tbody.append(el('tr', {}, el('td', { colSpan: 5, className: 'empty-state', textContent: t('as.noOps') })));
    return;
  }

  for (const h of list) {
    const dirStr = h.direction || '';
    let toProv = h.target || h.toProvider || '';
    if (!toProv && dirStr) {
      const parts = dirStr.split(/2|->/).map((s) => s.trim()).filter(Boolean);
      if (parts.length === 2) toProv = parts[1];
    }
    const targetId = h.targetId || h.target_id || '';
    const sourceId = h.sourceId || h.source_id || '';
    const resumeCmd = h.resumeCmd || formatResumeCmd(toProv, targetId);
    const statusText = h.status || 'ok';
    const isErr = statusText === 'error' || statusText === 'failed';
    const isSkip = statusText === 'skipped';
    const tsTip = h.timestampLocal || formatLocalDate(h.timestamp, h.timestamp || '');

    const dirCell = el('td', {},
      el('span', {
        className: 'pbadge ' + providerBadgeClass(toProv),
        textContent: dirStr || '-',
      }),
      h.kind ? el('div', { className: 'act-sub', textContent: h.kind, style: { marginTop: '3px' } }) : null
    );

    const sessionCell = el('td', {},
      el('div', { className: 'act-title', textContent: h.title || sourceId || t('row.untitled') }),
      el('div', { className: 'act-sub mono', textContent: h.cwd || t('row.noWorkspace') })
    );

    const statusCell = el('td', {},
      el('span', { className: 'status-pill ' + (isErr ? 'err' : isSkip ? '' : 'ok'), textContent: statusText }),
      h.message ? el('div', { className: 'act-sub', textContent: h.message, style: { marginTop: '3px' } }) : null
    );

    const targetCell = el('td', {});
    if (targetId) {
      targetCell.append(el('div', { className: 'act-sub mono', textContent: targetId.slice(0, 12) + (targetId.length > 12 ? '...' : ''), title: targetId }));
    }
    if (resumeCmd) {
      const btn = el('button', {
        type: 'button',
        className: 'sm',
        textContent: t('drawer.copyCmd'),
        title: resumeCmd,
        style: { marginTop: '3px' },
        onclick: () => copyText(resumeCmd, btn),
      });
      targetCell.append(btn);
    } else if (!targetId) {
      targetCell.append(el('span', { className: 'act-sub', textContent: '-' }));
    }

    tbody.append(el('tr', {},
      el('td', { textContent: formatRelative(h.timestamp, h.timestampLocal || h.timestamp || ''), title: tsTip, style: { whiteSpace: 'nowrap' } }),
      dirCell,
      sessionCell,
      statusCell,
      targetCell
    ));
  }
}

export function renderAutosyncStatus(as, workspaces) {
  const enabled = !!as.enabled;
  const running = !!as.running;

  const pill = $('autosyncPill');
  pill.className = 'autosync-pill' + (enabled ? ' on' : '');
  const dot = $('autosyncDot');
  dot.className = 'dot' + (running ? ' pulse' : enabled ? ' ok' : '');
  $('autosyncPillText').textContent = running
    ? t('as.pillSyncing')
    : enabled
      ? t('as.pillOn', { s: as.intervalSec || 30 })
      : t('as.pillOff');

  const tabBadge = $('tabAutosyncBadge');
  tabBadge.textContent = running ? t('tabs.badgeSync') : enabled ? t('tabs.badgeOn') : t('tabs.badgeOff');
  tabBadge.className = 'tab-badge' + (enabled ? ' on' : '');

  const statusBadge = $('asStatusBadge');
  statusBadge.textContent = running ? t('as.runningNow') : enabled ? t('as.enabled') : t('as.disabled');
  statusBadge.className = 'status-pill' + (running ? ' warn' : enabled ? ' ok' : '');

  $('asLastRun').textContent = as.lastRunAt
    ? t('as.lastRunAt', { rel: formatRelative(as.lastRunAt, as.lastRunAt), abs: formatLocalDate(as.lastRunAt, as.lastRunAt) })
    : t('as.lastRunNever');

  const errBox = $('asErrorBanner');
  if (as.lastError) {
    errBox.textContent = t('as.lastErr', { err: as.lastError });
    errBox.hidden = false;
  } else {
    errBox.hidden = true;
  }

  const ivSel = $('asInterval');
  for (const opt of ivSel.options) {
    if (opt.value) opt.textContent = t('as.everySec', { s: opt.value });
  }

  // Only populate form inputs when user does not have unsaved edits
  if (!state.autosyncDirty) {
    $('asEnabled').checked = enabled;
    const dirSel = $('asDirection');
    const dirVal = as.direction || 'both';
    dirSel.value = [...dirSel.options].some((o) => o.value === dirVal) ? dirVal : 'both';
    const iv = String(as.intervalSec || 30);
    if (![...ivSel.options].some((o) => o.value === iv)) {
      ivSel.append(el('option', { value: iv, textContent: t('as.everySec', { s: iv }) }));
    }
    ivSel.value = iv;
    $('asScope').value = as.scope || 'all';
    $('asIncludeThinking').checked = !!as.includeThinking;
    state.watchedDraft = Array.isArray(as.watchedFolders) ? [...as.watchedFolders] : [];
  }

  renderWatchedFolders(workspaces);
  renderHistory(as.history || []);
  renderAutosyncBasicSummary();
  syncAutosyncActionButtons();
}

export async function saveAutosync(runNow = false) {
  await withBusy(async () => {
    try {
      const payload = runNow && !state.autosyncDirty ? { runNow: true } : collectAutosyncPayload(runNow);
      const r = await api('autosync', payload);
      clearAutosyncDirty();
      toast(r.message || (runNow ? t('as.syncDoneToast') : t('as.savedToast')), 'ok');
      if (r.status) {
        renderStatus(r.status);
      } else {
        if (r.autosync) {
          if (!state.status) state.status = {};
          state.status.autosync = r.autosync;
          renderAutosyncStatus(r.autosync, state.status.workspaces || []);
        }
        await fetchStatus();
      }
      if (runNow) await fetchSessions();
    } catch (e) {
      toast(e.message, 'err');
    }
  });
}

export function initAutosyncPanel() {
  $('asEnabled').onchange = markAutosyncDirty;
  $('asDirection').onchange = markAutosyncDirty;
  $('asInterval').onchange = () => {
    markAutosyncDirty();
    renderAutosyncBasicSummary();
  };
  $('asIncludeThinking').onchange = () => {
    markAutosyncDirty();
    renderAutosyncBasicSummary();
  };
  $('asScope').onchange = () => {
    markAutosyncDirty();
    renderWatchedFolders(state.status ? state.status.workspaces : []);
    renderAutosyncBasicSummary();
  };
  $('watchAllBtn').onclick = () => {
    const ws = (state.status && state.status.workspaces) || [];
    state.watchedDraft = ws.map((w) => w.cwd).filter(Boolean);
    markAutosyncDirty();
    renderWatchedFolders(ws);
    renderAutosyncBasicSummary();
  };
  $('watchClearBtn').onclick = () => {
    state.watchedDraft = [];
    markAutosyncDirty();
    renderWatchedFolders(state.status ? state.status.workspaces : []);
    renderAutosyncBasicSummary();
  };
  $('watchAddBtn').onclick = () => {
    const p = $('watchAddInput').value.trim();
    if (!p) return;
    if (!state.watchedDraft.some((x) => normalizePathKey(x) === normalizePathKey(p))) {
      state.watchedDraft.push(p);
      markAutosyncDirty();
      renderWatchedFolders(state.status ? state.status.workspaces : []);
      renderAutosyncBasicSummary();
    }
    $('watchAddInput').value = '';
  };
  $('watchAddInput').onkeydown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      $('watchAddBtn').click();
    }
  };
  $('asSaveBtn').onclick = () => saveAutosync(false);
  $('asSyncNowBtn').onclick = () => saveAutosync(true);
}
