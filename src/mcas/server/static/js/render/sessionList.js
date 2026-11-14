import { state } from '../state.js';
import { $, el, svgIcon } from '../utils/dom.js';
import {
  formatBytes,
  formatExtra,
  formatLocalDate,
  formatRelative,
  groupKey,
  oppositeProvider,
} from '../utils/format.js';
import { copyText } from '../components/toast.js';
import { buildProviderDropdownMenu, buildProviderTagButton } from '../providerDropdown.js';
import { renderFilterPills, renderWorkspaceSelect } from './status.js';
import { fetchSessions } from '../actions.js';
import { openPreviewGroup } from '../drawer.js';
import { openTrimModal } from '../modals/trimModal.js';
import { t } from '../i18n.js';

export function setCwdFilter(cwd) {
  state.cwdFilter = (cwd || '').trim();
  state.cwdCustomMode = false;
  state.limit = 200;
  $('cwdCustomWrap').hidden = true;
  renderWorkspaceSelect(state.status ? state.status.workspaces : []);
  renderActiveFilters();
  fetchSessions({ silent: false });
}

export function clearCwdFilter(reload = true) {
  state.cwdFilter = '';
  state.cwdCustomMode = false;
  state.limit = 200;
  $('cwdCustomWrap').hidden = true;
  $('cwdCustomInput').value = '';
  renderWorkspaceSelect(state.status ? state.status.workspaces : []);
  renderActiveFilters();
  if (reload) fetchSessions({ silent: false });
}

export function renderActiveFilters() {
  const host = $('activeFilters');
  host.replaceChildren();
  const addChip = (label, onClear) => {
    host.append(el('span', { className: 'filter-chip' },
      el('span', { textContent: label }),
      el('button', { type: 'button', textContent: '\u00d7', title: t('filter.clearChipTitle'), onclick: onClear })
    ));
  };
  if (state.sourceFilter !== 'all') {
    addChip(t('filter.chipProvider', { val: state.sourceFilter }), () => {
      state.sourceFilter = 'all';
      renderFilterPills();
      fetchSessions();
    });
  }
  if (state.cwdFilter) {
    addChip(t('filter.chipFolder', { val: state.cwdFilter }), () => clearCwdFilter());
  }
  if (state.q) {
    addChip(t('filter.chipSearch', { val: state.q }), () => {
      state.q = '';
      $('searchInput').value = '';
      $('searchClear').hidden = true;
      fetchSessions();
    });
  }
}

export function updateSelectionUI() {
  const count = state.selected.size;
  let totalBytes = 0;
  for (const g of state.selected.values()) {
    totalBytes += Number(g.sizeBytes) || 0;
  }
  $('selCount').textContent = count
    ? t('sel.countBytes', { n: count, size: formatBytes(totalBytes) })
    : t('sel.zero');
  $('selectionBar').hidden = count === 0;

  if (count > 0) {
    const sources = new Set([...state.selected.values()].flatMap((g) => g.providers || []));
    if (sources.size === 1) {
      const only = [...sources][0];
      $('bulkTarget').value = oppositeProvider(only);
    }
  }

  const visibleKeys = state.groups.map(groupKey);
  const checkedVisible = visibleKeys.filter((k) => state.selected.has(k)).length;
  const selAll = $('selectAll');
  selAll.checked = visibleKeys.length > 0 && checkedVisible === visibleKeys.length;
  selAll.indeterminate = checkedVisible > 0 && checkedVisible < visibleKeys.length;
}

export function renderSessions() {
  const list = $('sessionList');
  list.replaceChildren();
  const groupCount = state.groups.length;
  const total = state.total ?? state.sessions.length;
  $('tabSessionsCount').textContent = String(groupCount);
  if (state.sessions.length !== groupCount) {
    $('listSummary').textContent = groupCount === 1
      ? t('list.countGroupedOne', { n: groupCount, raw: state.sessions.length })
      : t('list.countGroupedMany', { n: groupCount, raw: state.sessions.length });
  } else {
    $('listSummary').textContent = groupCount === 1
      ? t('list.countOne', { n: groupCount })
      : t('list.countMany', { n: groupCount });
  }

  renderActiveFilters();

  if (!state.groups.length) {
    const hasFilter = state.sourceFilter !== 'all' || !!state.cwdFilter || !!state.q || state.includeAll;
    const empty = el('li', { className: 'empty-state' },
      el('p', { textContent: hasFilter ? t('list.emptyFiltered') : t('list.emptyNone') })
    );
    if (state.sourceFilter !== 'all' || state.cwdFilter || state.q) {
      const btnRow = el('div', { style: { display: 'flex', gap: '8px', justifyContent: 'center', flexWrap: 'wrap' } });
      if (state.cwdFilter && state.sourceFilter !== 'all') {
        btnRow.append(el('button', {
          type: 'button',
          className: 'sm primary',
          textContent: t('list.showAllProviders'),
          onclick: () => {
            state.sourceFilter = 'all';
            renderFilterPills();
            fetchSessions();
          },
        }));
      }
      btnRow.append(el('button', {
        type: 'button',
        className: 'sm',
        textContent: t('list.resetFilters'),
        onclick: () => {
          state.sourceFilter = 'all';
          state.q = '';
          $('searchInput').value = '';
          $('searchClear').hidden = true;
          clearCwdFilter(false);
          renderFilterPills();
          fetchSessions();
        },
      }));
      empty.append(btnRow);
    }
    list.append(empty);
    $('listFoot').hidden = true;
    updateSelectionUI();
    return;
  }

  state.groups.forEach((g, idx) => {
    const key = groupKey(g);
    const isChecked = state.selected.has(key);
    const menuOpen = state.openDropdownKey === key;
    const row = el('li', {
      className: 'session-row' + (isChecked ? ' selected' : '') + (menuOpen ? ' menu-open' : ''),
    });

    const cb = el('input', {
      type: 'checkbox',
      className: 'row-check',
      checked: isChecked,
      ariaLabel: t('row.selectAria', { title: g.title || g.primary.id }),
      onclick: (e) => {
        if (e.shiftKey && state.lastCheckedIdx >= 0) {
          const lo = Math.min(state.lastCheckedIdx, idx);
          const hi = Math.max(state.lastCheckedIdx, idx);
          for (let i = lo; i <= hi; i++) {
            const item = state.groups[i];
            if (cb.checked) state.selected.set(groupKey(item), item);
            else state.selected.delete(groupKey(item));
          }
          renderSessions();
        } else {
          if (cb.checked) state.selected.set(key, g);
          else state.selected.delete(key);
          row.classList.toggle('selected', cb.checked);
          updateSelectionUI();
        }
        state.lastCheckedIdx = idx;
      },
    });

    const toggleDropdown = () => {
      state.openDropdownKey = state.openDropdownKey === key ? null : key;
      renderSessions();
    };

    const titleLine = el('div', { className: 'row-title-line' },
      buildProviderTagButton(g, toggleDropdown),
      el('span', {
        className: 'session-title',
        textContent: g.title || t('row.untitled'),
        onclick: () => openPreviewGroup(g),
      })
    );

    const extraTxt = formatExtra(g.extra);
    if (extraTxt) {
      titleLine.append(el('span', { className: 'extra-badge', textContent: extraTxt }));
    }

    const main = el('div', { className: 'row-main' }, titleLine);

    if (g.preview && g.preview !== g.title) {
      main.append(el('div', { className: 'session-preview', textContent: g.preview, title: g.preview }));
    }

    const uniqueIds = [...new Set(g.membersList.map((m) => m.id))];
    const idLabel = uniqueIds.length === 1
      ? uniqueIds[0].slice(0, 8)
      : g.providers.map((p) => `${p}:${g.members[p].id.slice(0, 8)}`).join(' \u00b7 ');
    const idTip = uniqueIds.length === 1
      ? t('row.idTipSingle', { id: uniqueIds[0] })
      : g.providers.map((p) => `${p}: ${g.members[p].id}`).join('\n');
    const idChip = el('span', {
      className: 'meta-pill mono clickable',
      textContent: idLabel,
      title: idTip,
      onclick: () => copyText(g.primary.id, idChip),
    });

    const folderChip = g.cwd
      ? el('button', {
          type: 'button',
          className: 'meta-pill clickable',
          title: t('row.filterFolderTip', { cwd: g.cwd }),
          onclick: () => setCwdFilter(g.cwd),
        },
        svgIcon('M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z', 12),
        el('span', { className: 'folder-text mono', textContent: g.cwd }))
      : el('span', { className: 'meta-pill', textContent: t('row.noWorkspace') });

    const countChip = el('span', {
      className: 'meta-pill',
      textContent: t('row.turns', { n: g.count ?? 0 }),
    });

    const sizeBreakdown = g.providers
      .map((p) => `${p}: ${g.members[p].sizeHuman || formatBytes(g.members[p].sizeBytes || 0)}`)
      .join(' \u00b7 ');
    const sizeChip = el('span', {
      className: 'meta-pill' + ((g.sizeBytes || 0) >= 10 * 1024 * 1024 ? ' heavy' : ''),
      textContent: g.sizeHuman || formatBytes(g.sizeBytes || 0),
      title: sizeBreakdown,
    });

    const timeChip = el('span', {
      className: 'meta-pill',
      textContent: formatRelative(g.updatedAt, g.updatedLocal || ''),
      title: g.updatedLocal || formatLocalDate(g.updatedAt),
    });

    main.append(el('div', { className: 'row-meta' }, idChip, folderChip, countChip, sizeChip, timeChip));

    const ddWrap = el('div', {
      className: 'prov-dropdown',
      'data-dropdown-host': 'true',
    },
      el('button', {
        type: 'button',
        className: 'sm',
        textContent: t('row.providersBtn', { list: g.providers.join(', ') }),
        'aria-expanded': menuOpen ? 'true' : 'false',
        onclick: (e) => {
          e.stopPropagation();
          toggleDropdown();
        },
      })
    );
    if (menuOpen) ddWrap.append(buildProviderDropdownMenu(g));

    const actions = el('div', { className: 'row-actions' },
      ddWrap,
      el('button', {
        type: 'button',
        className: 'sm',
        textContent: t('row.lighten'),
        title: t('row.lightenTitle'),
        onclick: () => openTrimModal(g),
      }),
      el('button', {
        type: 'button',
        className: 'sm',
        textContent: t('row.preview'),
        onclick: () => openPreviewGroup(g),
      })
    );

    row.append(cb, main, actions);
    list.append(row);
  });

  if (total > state.sessions.length) {
    $('listFoot').hidden = false;
    $('listFootText').textContent = t('list.footShowing', { shown: state.sessions.length, grouped: groupCount, total });
  } else {
    $('listFoot').hidden = true;
  }

  updateSelectionUI();
}
