import { isAdvanced, state } from '../state.js';
import { api } from '../api.js';
import { $, el } from '../utils/dom.js';
import { withBusy } from '../utils/busy.js';
import { toast } from '../components/toast.js';
import { askConfirm } from '../components/confirmModal.js';
import { closeProviderDropdown } from '../providerDropdown.js';
import { renderStatus } from '../render/status.js';
import { fetchSessions, fetchStatus } from '../actions.js';
import { t } from '../i18n.js';

export function renderStorageSummaryGrid() {
  const host = $('clStorageGrid');
  const st = (state.status && state.status.storage) || {};
  host.replaceChildren(
    el('div', { className: 'storage-stat wide' },
      el('span', { className: 'lbl', textContent: t('cleanup.totalStorage') }),
      el('span', { className: 'val', textContent: st.totalHuman || '0 B' })
    ),
    el('div', { className: 'storage-stat' },
      el('span', { className: 'lbl', textContent: t('cleanup.agyLabel') }),
      el('span', { className: 'val', textContent: st.agyHuman || '0 B' })
    ),
    el('div', { className: 'storage-stat' },
      el('span', { className: 'lbl', textContent: t('cleanup.claudeLabel') }),
      el('span', { className: 'val', textContent: st.claudeHuman || '0 B' })
    ),
    el('div', { className: 'storage-stat' },
      el('span', { className: 'lbl', textContent: t('cleanup.codexLabel') }),
      el('span', { className: 'val', textContent: st.codexHuman || '0 B' })
    ),
    el('div', { className: 'storage-stat' },
      el('span', { className: 'lbl', textContent: t('cleanup.orphansSummaryLabel') }),
      el('span', {
        className: 'val',
        textContent: `${st.orphanHuman || '0 B'} \u00b7 ${st.orphanCount || 0} / ${st.emptyCount || 0} / ${st.subagentCount || 0}`,
      })
    )
  );
}

export function openCleanupModal() {
  closeProviderDropdown(false);
  renderStorageSummaryGrid();
  $('clPreviewBox').hidden = true;
  $('clPreviewList').replaceChildren();
  $('clScope').value = state.cwdFilter ? 'cwd' : 'all';
  $('cleanupModal').hidden = false;
}

export async function runStorageClean(dryRun) {
  const adv = isAdvanced();
  const olderRaw = adv ? $('clOlderDays').value.trim() : '';
  const largerRaw = adv ? $('clLargerMb').value.trim() : '';
  const scopeVal = adv ? $('clScope').value : 'all';
  const payload = {
    source: state.sourceFilter || 'all',
    cwd: scopeVal === 'cwd' ? (state.cwdFilter || '') : '',
    cleanOrphans: $('clOrphans').checked,
    deleteEmpty: $('clEmpty').checked,
    deleteSubagents: $('clSubagents').checked,
    olderThanDays: olderRaw ? Number(olderRaw) : null,
    largerThanMb: largerRaw ? Number(largerRaw) : null,
    vacuumDb: true,
    dryRun: !!dryRun,
  };

  if (!dryRun) {
    const ok = await askConfirm(
      t('cleanup.confirmTitle'),
      t('cleanup.confirmMsg'),
      t('cleanup.runBtn'),
      true
    );
    if (!ok) return;
  }

  await withBusy(async () => {
    try {
      const r = await api('clean', payload);
      if (dryRun) {
        $('clPreviewBox').hidden = false;
        $('clPreviewSummary').textContent = r.message || t('cleanup.estimatedFreed', { freed: r.freedHuman || '0 B' });
        const ul = $('clPreviewList');
        ul.replaceChildren();
        const items = Array.isArray(r.previewItems) ? r.previewItems : [];
        if (!items.length) {
          ul.append(el('li', { textContent: t('cleanup.nothingMatched') }));
        } else {
          for (const it of items.slice(0, 60)) {
            ul.append(el('li', { textContent: `[${it.kind || ''}] ${it.label || ''} (${it.human || it.sizeHuman || '0 B'})` }));
          }
          if (items.length > 60) {
            ul.append(el('li', { textContent: t('cleanup.moreItems', { n: items.length - 60 }) }));
          }
        }
      } else {
        toast(r.message || t('cleanup.done'), 'ok');
        $('clPreviewBox').hidden = true;
        if (r.status) renderStatus(r.status);
        else await fetchStatus();
        renderStorageSummaryGrid();
        await fetchSessions();
      }
    } catch (e) {
      toast(e.message, 'err');
    }
  });
}

export function initCleanupModal() {
  $('openCleanupBtn').onclick = openCleanupModal;
  $('clCloseBtn').onclick = () => { $('cleanupModal').hidden = true; };
  $('clDoneBtn').onclick = () => { $('cleanupModal').hidden = true; };
  $('cleanupModal').onclick = (e) => { if (e.target === $('cleanupModal')) $('cleanupModal').hidden = true; };
  $('clPreviewBtn').onclick = () => runStorageClean(true);
  $('clRunBtn').onclick = () => runStorageClean(false);
}
