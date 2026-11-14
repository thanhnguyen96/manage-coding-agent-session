import { state } from '../state.js';
import { api } from '../api.js';
import { $, el } from '../utils/dom.js';
import { normalizePathKey, providerLabel } from '../utils/format.js';
import { withBusy } from '../utils/busy.js';
import { summarizeBatchToast, toast } from '../components/toast.js';
import { renderStatus } from '../render/status.js';
import { fetchSessions, fetchStatus } from '../actions.js';

export function updateSyncDirHint() {
  const selVal = $('sdCwdSelect').value;
  const cwd = selVal === '__custom__' ? $('sdCwdCustom').value.trim() : selVal;
  const [fromP, toP] = $('sdDirection').value.split('->');
  const workspaces = (state.status && state.status.workspaces) || [];
  const ws = workspaces.find((w) => normalizePathKey(w.cwd) === normalizePathKey(cwd));
  if (ws && ws.counts) {
    const cnt = ws.counts[fromP] ?? 0;
    $('sdCountHint').textContent = `${cnt} ${providerLabel(fromP)} session(s) detected in this folder -> will copy to ${providerLabel(toP)}.`;
  } else {
    $('sdCountHint').textContent = cwd
      ? `Will copy all top-level ${providerLabel(fromP)} sessions in "${cwd}" to ${providerLabel(toP)}.`
      : 'Choose a workspace folder to sync.';
  }
}

export function openSyncDirModal() {
  const sel = $('sdCwdSelect');
  sel.replaceChildren();
  const workspaces = (state.status && state.status.workspaces) || [];
  let hasPreselected = false;
  for (const w of workspaces) {
    if (!w || !w.cwd) continue;
    const opt = el('option', {
      value: w.cwd,
      textContent: `${w.cwd} (agy ${w.counts?.agy ?? 0} / claude ${w.counts?.claude ?? 0})`,
    });
    if (state.cwdFilter && normalizePathKey(w.cwd) === normalizePathKey(state.cwdFilter)) {
      opt.selected = true;
      hasPreselected = true;
    }
    sel.append(opt);
  }
  const customOpt = el('option', { value: '__custom__', textContent: 'Custom folder path...' });
  if (state.cwdFilter && !hasPreselected) customOpt.selected = true;
  if (!workspaces.length) customOpt.selected = true;
  sel.append(customOpt);

  if (sel.value === '__custom__') {
    $('sdCwdCustom').hidden = false;
    $('sdCwdCustom').value = state.cwdFilter || '';
  } else {
    $('sdCwdCustom').hidden = true;
    $('sdCwdCustom').value = '';
  }
  if (state.sourceFilter === 'claude') $('sdDirection').value = 'claude->agy';
  else $('sdDirection').value = 'agy->claude';
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
      toast('Please enter or select a workspace folder.', 'err');
      return;
    }
    const [fromProvider, toProvider] = $('sdDirection').value.split('->');
    const targetCwd = $('sdTargetCwd').value.trim();
    const includeThinking = $('sdThinking').checked;
    const overwrite = $('sdOverwrite').checked;
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
        summarizeBatchToast(r, `Synced ${fromProvider} -> ${toProvider} for ${cwd}`);
        if (r.status) renderStatus(r.status);
        else await fetchStatus();
        await fetchSessions();
      } catch (err) {
        toast(err.message, 'err');
      }
    });
  };
}
