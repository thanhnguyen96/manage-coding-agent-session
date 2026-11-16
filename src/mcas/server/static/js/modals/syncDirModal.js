import { isAdvanced, state } from '../state.js';
import { api } from '../api.js';
import { $, el } from '../utils/dom.js';
import {
  formatWorkspaceLabel,
  normalizePathKey,
  oppositeProvider,
  providerLabel,
  supportedProviderIds,
} from '../utils/format.js';
import { withBusy } from '../utils/busy.js';
import { summarizeBatchToast, toast } from '../components/toast.js';
import { renderStatus } from '../render/status.js';
import { fetchSessions, fetchStatus } from '../actions.js';
import { t } from '../i18n.js';

export function updateSyncDirHint() {
  const selVal = $('sdCwdSelect').value;
  const cwd = selVal === '__custom__' ? $('sdCwdCustom').value.trim() : selVal;
  const [fromP, toP] = $('sdDirection').value.split('->');
  const workspaces = (state.status && state.status.workspaces) || [];
  const ws = workspaces.find((w) => normalizePathKey(w.cwd) === normalizePathKey(cwd));
  if (ws && ws.counts) {
    const cnt = ws.counts[fromP] ?? 0;
    $('sdCountHint').textContent = t('syncDir.hintDetected', { n: cnt, from: providerLabel(fromP), to: providerLabel(toP) });
  } else {
    $('sdCountHint').textContent = cwd
      ? t('syncDir.hintCustom', { from: providerLabel(fromP), cwd, to: providerLabel(toP) })
      : t('syncDir.hintChoose');
  }
}

export function renderSyncDirWorkspaceOptions() {
  const sel = $('sdCwdSelect');
  const prev = sel.value;
  sel.replaceChildren();
  const workspaces = (state.status && state.status.workspaces) || [];
  const activeIds = supportedProviderIds(state.status && state.status.providers);
  let hasPreselected = false;
  for (const w of workspaces) {
    if (!w || !w.cwd) continue;
    const opt = el('option', {
      value: w.cwd,
      textContent: formatWorkspaceLabel(w, activeIds),
    });
    if (prev ? normalizePathKey(w.cwd) === normalizePathKey(prev) : (state.cwdFilter && normalizePathKey(w.cwd) === normalizePathKey(state.cwdFilter))) {
      opt.selected = true;
      hasPreselected = true;
    }
    sel.append(opt);
  }
  const customOpt = el('option', { value: '__custom__', textContent: t('filter.customFolderOpt') });
  if (prev === '__custom__' || (!prev && state.cwdFilter && !hasPreselected) || !workspaces.length) {
    customOpt.selected = true;
  }
  sel.append(customOpt);
}

export function openSyncDirModal() {
  const sel = $('sdCwdSelect');
  sel.value = '';
  renderSyncDirWorkspaceOptions();

  if (sel.value === '__custom__') {
    $('sdCwdCustom').hidden = false;
    $('sdCwdCustom').value = state.cwdFilter || '';
  } else {
    $('sdCwdCustom').hidden = true;
    $('sdCwdCustom').value = '';
  }
  if (state.sourceFilter && state.sourceFilter !== 'all') {
    const tgt = oppositeProvider(state.sourceFilter, state.status && state.status.providers);
    const candidate = `${state.sourceFilter}->${tgt}`;
    const dirSel = $('sdDirection');
    if ([...dirSel.options].some((o) => o.value === candidate)) {
      dirSel.value = candidate;
    } else {
      dirSel.value = 'agy->claude';
    }
  } else {
    $('sdDirection').value = 'agy->claude';
  }
  $('sdTargetCwd').value = '';
  updateSyncDirHint();
  $('syncDirModal').hidden = false;
}

export function initSyncDirModal() {
  $('openSyncDirBtn').onclick = openSyncDirModal;
  $('sdCloseBtn').onclick = () => { $('syncDirModal').hidden = true; };
  $('sdCancelBtn').onclick = () => { $('syncDirModal').hidden = true; };
  $('syncDirModal').onclick = (e) => { if (e.target === $('syncDirModal')) $('syncDirModal').hidden = true; };
  $('sdCwdSelect').onchange = () => {
    $('sdCwdCustom').hidden = $('sdCwdSelect').value !== '__custom__';
    updateSyncDirHint();
  };
  $('sdCwdCustom').oninput = updateSyncDirHint;
  $('sdDirection').onchange = updateSyncDirHint;
  $('syncDirForm').onsubmit = async (e) => {
    e.preventDefault();
    const selVal = $('sdCwdSelect').value;
    const cwd = (selVal === '__custom__' ? $('sdCwdCustom').value : selVal).trim();
    if (!cwd) {
      toast(t('syncDir.errNoFolder'), 'err');
      return;
    }
    const [fromProvider, toProvider] = $('sdDirection').value.split('->');
    const adv = isAdvanced();
    const targetCwd = adv ? $('sdTargetCwd').value.trim() : '';
    const includeThinking = adv ? $('sdThinking').checked : false;
    const overwrite = adv ? $('sdOverwrite').checked : false;
    $('syncDirModal').hidden = true;

    await withBusy(async () => {
      try {
        const r = await api('sync-dir', {
          fromProvider,
          toProvider,
          cwd,
          targetCwd,
          overwrite,
          includeThinking,
        });
        summarizeBatchToast(r, t('syncDir.doneFallback', { from: fromProvider, to: toProvider, cwd }));
        if (r.status) renderStatus(r.status);
        else await fetchStatus();
        await fetchSessions();
      } catch (err) {
        toast(err.message, 'err');
      }
    });
  };
}
