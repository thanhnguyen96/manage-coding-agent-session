import { state } from '../state.js';
import { api } from '../api.js';
import { $, el } from '../utils/dom.js';
import { withBusy } from '../utils/busy.js';
import {
  formatLocalDate,
  formatRelative,
  formatResumeCmd,
  normalizePathKey,
} from '../utils/format.js';
import { copyText, toast } from '../components/toast.js';
import { renderStatus } from '../render/status.js';
import { fetchSessions, fetchStatus } from '../actions.js';

export function markAutosyncDirty() {
  state.autosyncDirty = true;
  $('asDirtyPill').hidden = false;
}

export function clearAutosyncDirty() {
  state.autosyncDirty = false;
  $('asDirtyPill').hidden = true;
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
    ? 'Watched folders (active scope)'
    : 'Watched folders (used when scope is "Selected folders only")';
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
    host.append(el('div', { className: 'brand-sub', textContent: 'No workspaces detected yet. Add a folder path below.' }));
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
      row.append(el('span', { className: 'brand-sub', textContent: `${ws.total ?? 0} sessions` }));
    } else {
      row.append(el('button', {
        type: 'button',
        className: 'sm ghost',
        textContent: 'Remove',
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
  $('historyCount').textContent = `${list.length} ${list.length === 1 ? 'entry' : 'entries'}`;
  const tbody = $('historyBody');
  tbody.replaceChildren();
  if (!list.length) {
    tbody.append(el('tr', {}, el('td', { colSpan: 5, className: 'empty-state', textContent: 'No sync operations recorded yet.' })));
    return;
  }

  for (const h of list) {
    const dirStr = h.direction || '';
    const toProv = h.target || (dirStr.includes('claude') && !dirStr.endsWith('agy')
      ? 'claude'
      : dirStr.endsWith('agy') ? 'agy' : (h.toProvider || ''));
    const targetId = h.targetId || h.target_id || '';
    const sourceId = h.sourceId || h.source_id || '';
    const resumeCmd = h.resumeCmd || formatResumeCmd(toProv, targetId);
    const statusText = h.status || 'ok';
    const isErr = statusText === 'error' || statusText === 'failed';
    const isSkip = statusText === 'skipped';
    const tsTip = h.timestampLocal || formatLocalDate(h.timestamp, h.timestamp || '');

    const dirCell = el('td', {},
      el('span', {
        className: 'pbadge ' + (toProv === 'agy' ? 'agy' : toProv === 'claude' ? 'claude' : 'other'),
        textContent: dirStr || '-',
      }),
      h.kind ? el('div', { className: 'act-sub', textContent: h.kind, style: { marginTop: '3px' } }) : null
    );

    const sessionCell = el('td', {},
      el('div', { className: 'act-title', textContent: h.title || sourceId || '(untitled)' }),
      el('div', { className: 'act-sub mono', textContent: h.cwd || '(no workspace)' })
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
        textContent: 'Copy cmd',
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
    ? 'Auto-Sync: syncing...'
    : enabled
      ? `Auto-Sync: ON (${as.intervalSec || 30}s)`
      : 'Auto-Sync: OFF';

  const tabBadge = $('tabAutosyncBadge');
  tabBadge.textContent = running ? 'SYNC' : enabled ? 'ON' : 'OFF';
  tabBadge.className = 'tab-badge' + (enabled ? ' on' : '');

  const statusBadge = $('asStatusBadge');
  statusBadge.textContent = running ? 'Running now' : enabled ? 'Enabled' : 'Disabled';
  statusBadge.className = 'status-pill' + (running ? ' warn' : enabled ? ' ok' : '');

  $('asLastRun').textContent = as.lastRunAt
    ? `Last run: ${formatRelative(as.lastRunAt, as.lastRunAt)} (${formatLocalDate(as.lastRunAt, as.lastRunAt)})`
    : 'Last run: never';

  const errBox = $('asErrorBanner');
  if (as.lastError) {
    errBox.textContent = `Last auto-sync error: ${as.lastError}`;
    errBox.hidden = false;
  } else {
    errBox.hidden = true;
  }

  // Only populate form inputs when user does not have unsaved edits
  if (!state.autosyncDirty) {
    $('asEnabled').checked = enabled;
    $('asDirection').value = as.direction || 'both';
    const iv = String(as.intervalSec || 30);
    const ivSel = $('asInterval');
    if (![...ivSel.options].some((o) => o.value === iv)) {
      ivSel.append(el('option', { value: iv, textContent: `Every ${iv}s` }));
    }
    ivSel.value = iv;
    $('asScope').value = as.scope || 'all';
    $('asIncludeThinking').checked = !!as.includeThinking;
    state.watchedDraft = Array.isArray(as.watchedFolders) ? [...as.watchedFolders] : [];
  }

  renderWatchedFolders(workspaces);
  renderHistory(as.history || []);
}

export async function saveAutosync(runNow = false) {
  await withBusy(async () => {
    try {
      const payload = runNow && !state.autosyncDirty ? { runNow: true } : collectAutosyncPayload(runNow);
      const r = await api('autosync', payload);
      clearAutosyncDirty();
      toast(r.message || (runNow ? 'Sync completed.' : 'Auto-Sync settings saved.'), 'ok');
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
  $('asInterval').onchange = markAutosyncDirty;
  $('asIncludeThinking').onchange = markAutosyncDirty;
  $('asScope').onchange = () => {
    markAutosyncDirty();
    renderWatchedFolders(state.status ? state.status.workspaces : []);
  };
  $('watchAllBtn').onclick = () => {
    const ws = (state.status && state.status.workspaces) || [];
    state.watchedDraft = ws.map((w) => w.cwd).filter(Boolean);
    markAutosyncDirty();
    renderWatchedFolders(ws);
  };
  $('watchClearBtn').onclick = () => {
    state.watchedDraft = [];
    markAutosyncDirty();
    renderWatchedFolders(state.status ? state.status.workspaces : []);
  };
  $('watchAddBtn').onclick = () => {
    const p = $('watchAddInput').value.trim();
    if (!p) return;
    if (!state.watchedDraft.some((x) => normalizePathKey(x) === normalizePathKey(p))) {
      state.watchedDraft.push(p);
      markAutosyncDirty();
      renderWatchedFolders(state.status ? state.status.workspaces : []);
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
