import { isAdvanced, state } from './state.js';
import { api } from './api.js';
import { $ } from './utils/dom.js';
import { formatBytes, groupKey } from './utils/format.js';
import { withBusy } from './utils/busy.js';
import { groupSessions } from './grouping.js';
import { extractResultCommands, summarizeBatchToast, toast } from './components/toast.js';
import { askConfirm } from './components/confirmModal.js';
import { renderStatus } from './render/status.js';
import { renderActiveFilters, renderSessions } from './render/sessionList.js';
import { closeProviderDropdown } from './providerDropdown.js';
import { closePreview } from './drawer.js';
import { t } from './i18n.js';

let pollTimer = null;

export function startPolling() {
  if (!pollTimer) {
    pollTimer = setInterval(() => refreshAll(false), 5000);
  }
}

export function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

export async function fetchStatus({ fromPoll = false } = {}) {
  const s = await api('status');
  renderStatus(s, { fromPoll });
}

export async function fetchSessions({ silent = false, rethrow = false } = {}) {
  const seq = ++state.sessionsReqSeq;
  state.filtering = !silent;
  if (!silent) {
    $('listSummary').textContent = t('list.loadingConvs');
    renderActiveFilters();
  }

  const params = new URLSearchParams();
  params.set('source', state.sourceFilter || 'all');
  if (state.cwdFilter) params.set('cwd', state.cwdFilter);
  if (state.q) params.set('q', state.q);
  params.set('all', state.includeAll ? '1' : '0');
  params.set('sort', state.sort || 'updated_desc');
  params.set('limit', String(state.limit));

  try {
    const data = await api('sessions?' + params.toString());
    if (seq !== state.sessionsReqSeq) return;
    state.filtering = false;
    state.sessions = Array.isArray(data.sessions) ? data.sessions : [];
    state.groups = groupSessions(state.sessions);
    state.total = typeof data.total === 'number' ? data.total : state.sessions.length;
    state.connFailures = 0;
    $('connBanner').hidden = true;
    renderSessions();
  } catch (e) {
    if (seq !== state.sessionsReqSeq) return;
    state.filtering = false;
    $('listSummary').textContent = t('conn.loadFailed');
    if (rethrow) throw e;
    if (!e.httpStatus) {
      state.connFailures += 1;
      $('connBannerText').textContent = t('conn.unreachable');
      $('connBanner').hidden = false;
    }
    if (!silent) {
      toast(e.message || t('conn.loadFailedToast'), 'err');
    }
  }
}

export async function refreshAll(manual = false) {
  if (state.stopped) return;
  if (!manual && (state.busy || state.openDropdownKey || document.hidden)) return;
  try {
    await Promise.all([
      fetchStatus({ fromPoll: !manual }),
      fetchSessions({ silent: !manual, rethrow: true }),
    ]);
    state.connFailures = 0;
    $('connBanner').hidden = true;
  } catch (e) {
    if (!e.httpStatus) {
      state.connFailures += 1;
      if (state.connFailures >= 2 || manual) {
        $('connBannerText').textContent = t('conn.unreachable');
        $('connBanner').hidden = false;
        if (state.connFailures >= 3) {
          stopPolling();
        }
      }
    } else if (manual) {
      toast(e.message, 'err');
    }
  }
}

export async function copySingleSession(source, id, toProvider, includeThinking) {
  await withBusy(async () => {
    try {
      const r = await api('copy', {
        items: [{ source, id }],
        toProvider,
        targetCwd: '',
        newId: false,
        overwrite: false,
        includeThinking: !!includeThinking,
      });
      summarizeBatchToast(r, t('copy.copiedTo', { to: toProvider }));
      if (r.status) renderStatus(r.status);
      else await fetchStatus();
      await fetchSessions();
    } catch (e) {
      toast(e.message, 'err');
    }
  });
}

export async function copySelectedSessions() {
  if (!state.selected.size) return;
  const adv = isAdvanced();
  const toProvider = $('bulkTarget').value;
  const targetCwd = adv ? $('bulkTargetCwd').value.trim() : '';
  const includeThinking = adv ? $('bulkThinking').checked : false;
  const newId = adv ? $('bulkNewId').checked : false;
  const overwrite = adv ? $('bulkOverwrite').checked : false;
  const items = [...state.selected.values()].map((g) => ({
    source: g.primary.source,
    id: g.primary.id,
  }));

  await withBusy(async () => {
    try {
      const r = await api('copy', {
        items,
        toProvider,
        targetCwd,
        newId,
        overwrite,
        includeThinking,
      });
      summarizeBatchToast(r, t('copy.copiedBatch', { n: items.length, to: toProvider }));
      state.selected.clear();
      if (r.status) renderStatus(r.status);
      else await fetchStatus();
      await fetchSessions();
    } catch (e) {
      toast(e.message, 'err');
    }
  });
}

export async function confirmDeleteGroup(g) {
  const title = g.title || (g.primary && g.primary.id) || 'conversation';
  const members = Array.isArray(g.membersList) && g.membersList.length ? g.membersList : [g.primary];
  const provDesc = (g.providers || []).join(', ');
  const sizeStr = g.sizeHuman || formatBytes(g.sizeBytes || 0);
  const copyWord = members.length === 1 ? t('delete.copyOne') : t('delete.copyMany');
  const ok = await askConfirm(
    t('delete.confirmTitle'),
    t('delete.confirmMsg', { title, providers: provDesc, count: members.length, copyWord, size: sizeStr }),
    t('delete.confirmBtn'),
    true
  );
  if (!ok) return false;

  await withBusy(async () => {
    try {
      const r = await api('delete', {
        items: members.map((m) => ({ source: m.source, id: m.id })),
        deleteSyncedPeers: true,
      });
      state.selected.delete(groupKey(g));
      if (
        state.drawerSession &&
        members.some((m) => m.source === state.drawerSession.source && m.id === state.drawerSession.id)
      ) {
        closePreview();
      }
      toast(r.message || t('delete.done'), 'ok');
      if (r.status) renderStatus(r.status);
      else await fetchStatus();
      await fetchSessions();
    } catch (e) {
      toast(e.message, 'err');
    }
  });
  return true;
}

export async function applyGroupProviders(g, desiredProviders, includeThinking) {
  closeProviderDropdown(false);
  if (!desiredProviders.length) {
    const deleted = await confirmDeleteGroup(g);
    if (!deleted) renderSessions();
    return;
  }

  const toAdd = desiredProviders.filter((p) => !g.members[p]);
  const toRemove = (g.providers || []).filter((p) => !desiredProviders.includes(p));
  if (!toAdd.length && !toRemove.length) {
    renderSessions();
    return;
  }

  await withBusy(async () => {
    let lastStatus = null;
    const cmds = [];
    const notes = [];
    try {
      // Add new providers first before any removal (handles provider move cleanly)
      for (const toProv of toAdd) {
        const r = await api('copy', {
          items: [{ source: g.primary.source, id: g.primary.id }],
          toProvider: toProv,
          targetCwd: '',
          newId: false,
          overwrite: true,
          includeThinking: !!includeThinking,
        });
        if (r.status) lastStatus = r.status;
        cmds.push(...extractResultCommands(r.results || []));
        notes.push(t('copy.addedTo', { to: toProv }));
      }

      if (toRemove.length) {
        const delItems = [];
        for (const p of toRemove) {
          const list = (g.membersByProv && g.membersByProv[p]) || (g.members[p] ? [g.members[p]] : []);
          for (const m of list) delItems.push({ source: m.source, id: m.id });
        }
        if (delItems.length) {
          const dr = await api('delete', { items: delItems, deleteSyncedPeers: false });
          if (dr.status) lastStatus = dr.status;
          notes.push(t('copy.removedFrom', { list: toRemove.join(', '), freed: dr.freedHuman || '0 B' }));
          if (
            state.drawerSession &&
            delItems.some((m) => m.source === state.drawerSession.source && m.id === state.drawerSession.id)
          ) {
            closePreview();
          }
        }
      }

      toast(notes.join(' \u00b7 ') || t('copy.providersUpdated'), 'ok', cmds);
      if (lastStatus) renderStatus(lastStatus);
      else await fetchStatus();
      await fetchSessions();
    } catch (e) {
      toast(e.message, 'err');
      await fetchSessions();
    }
  });
}

export async function confirmDeleteSelected() {
  if (!state.selected.size) return;
  const groups = [...state.selected.values()];
  const items = [];
  let totalBytes = 0;
  for (const g of groups) {
    totalBytes += Number(g.sizeBytes) || 0;
    const members = Array.isArray(g.membersList) && g.membersList.length ? g.membersList : [g.primary];
    for (const m of members) {
      items.push({ source: m.source, id: m.id });
    }
  }
  const ok = await askConfirm(
    t('delete.bulkTitle', { n: groups.length }),
    t('delete.bulkMsg', { n: groups.length, copies: items.length, size: formatBytes(totalBytes) }),
    t('sel.deleteSelected'),
    true
  );
  if (!ok) return;

  await withBusy(async () => {
    try {
      const r = await api('delete', { items, deleteSyncedPeers: true });
      state.selected.clear();
      if (
        state.drawerSession &&
        items.some((m) => m.source === state.drawerSession.source && m.id === state.drawerSession.id)
      ) {
        closePreview();
      }
      toast(r.message || t('delete.bulkDone'), 'ok');
      if (r.status) renderStatus(r.status);
      else await fetchStatus();
      await fetchSessions();
    } catch (e) {
      toast(e.message, 'err');
    }
  });
}
