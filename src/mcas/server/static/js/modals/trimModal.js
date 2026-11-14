import { state } from '../state.js';
import { api } from '../api.js';
import { $ } from '../utils/dom.js';
import { formatBytes } from '../utils/format.js';
import { withBusy } from '../utils/busy.js';
import { toast } from '../components/toast.js';
import { closeProviderDropdown } from '../providerDropdown.js';
import { renderStatus } from '../render/status.js';
import { fetchSessions, fetchStatus } from '../actions.js';
import { openPreview } from '../drawer.js';

export function openTrimModal(g) {
  closeProviderDropdown(false);
  state.trimTarget = g;
  const provStr = (g.providers || []).join(', ');
  $('tmSessionLabel').textContent =
    `${g.title || g.primary.id} \u00b7 (${provStr}) \u00b7 ${g.count ?? 0} turns \u00b7 ${g.sizeHuman || formatBytes(g.sizeBytes || 0)}`;
  $('trimModal').hidden = false;
  $('tmKeepPreset').focus();
}

export function initTrimModal() {
  $('tmCloseBtn').onclick = () => { $('trimModal').hidden = true; };
  $('tmCancelBtn').onclick = () => { $('trimModal').hidden = true; };
  $('trimModal').onclick = (e) => { if (e.target === $('trimModal')) $('trimModal').hidden = true; };
  $('trimForm').onsubmit = async (e) => {
    e.preventDefault();
    const g = state.trimTarget;
    if (!g) return;
    const preset = $('tmKeepPreset').value;
    const keepRatio = preset === '50' ? 0.5 : (preset === 'all' ? 1.0 : null);
    const keepLastTurns = preset === 'last20' ? 20 : (preset === 'last50' ? 50 : null);
    const maxToolOutputChars = $('tmTruncateTools').checked ? 2000 : 0;
    const stripThinking = $('tmStripThinking').checked;
    const purgeSidecars = $('tmPurgeSidecars').checked;
    const members = Array.isArray(g.membersList) && g.membersList.length ? g.membersList : [g.primary];

    await withBusy(async () => {
      let totalBefore = 0;
      let totalAfter = 0;
      let totalFreed = 0;
      let lastStatus = null;
      try {
        for (const m of members) {
          const r = await api('trim', {
            source: m.source,
            id: m.id,
            keepRatio,
            keepLastTurns,
            maxToolOutputChars,
            stripThinking,
            purgeSidecars,
          });
          totalBefore += Number(r.beforeBytes) || 0;
          totalAfter += Number(r.afterBytes) || 0;
          totalFreed += Number(r.freedBytes) || 0;
          if (r.status) lastStatus = r.status;
        }
        $('trimModal').hidden = true;
        toast(
          `Lightened (${members.length} ${members.length === 1 ? 'copy' : 'copies'}): ${formatBytes(totalBefore)} \u2192 ${formatBytes(totalAfter)} (freed ${formatBytes(totalFreed)})`,
          'ok'
        );
        if (lastStatus) renderStatus(lastStatus);
        else await fetchStatus();
        await fetchSessions();
        if (
          state.drawerSession &&
          members.some((m) => m.source === state.drawerSession.source && m.id === state.drawerSession.id)
        ) {
          const freshGroup = state.groups.find((x) => x.groupKey === g.groupKey) || state.drawerGroup;
          await openPreview(state.drawerSession.source, state.drawerSession.id, freshGroup);
        }
      } catch (err) {
        toast(err.message, 'err');
      }
    });
  };
}
