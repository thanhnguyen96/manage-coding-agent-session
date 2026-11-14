import { state, storageSet } from '../state.js';
import { $, el } from '../utils/dom.js';
import { normalizePathKey } from '../utils/format.js';
import { renderAutosyncStatus } from '../panels/autosync.js';
import { fetchSessions } from '../actions.js';
import { getLang, t } from '../i18n.js';

export function switchTab(tab) {
  state.tab = tab === 'autosync' ? 'autosync' : 'sessions';
  const isSessions = state.tab === 'sessions';
  $('panelSessions').hidden = !isSessions;
  $('panelAutosync').hidden = isSessions;
  $('tabBtnSessions').classList.toggle('active', isSessions);
  $('tabBtnAutosync').classList.toggle('active', !isSessions);
  $('tabBtnSessions').setAttribute('aria-selected', String(isSessions));
  $('tabBtnAutosync').setAttribute('aria-selected', String(!isSessions));
  storageSet('mcas.tab', state.tab);
}

export function renderFilterPills(providers) {
  const list = providers || (state.status && state.status.providers) || [
    { id: 'agy', name: 'Antigravity', supported: true },
    { id: 'claude', name: 'Claude Code', supported: true },
  ];
  const host = $('providerPills');
  host.replaceChildren();

  const allBtn = el('button', {
    type: 'button',
    className: state.sourceFilter === 'all' ? 'active' : '',
    textContent: t('filter.all'),
    onclick: () => {
      state.sourceFilter = 'all';
      renderFilterPills();
      fetchSessions();
    },
  });
  host.append(allBtn);

  for (const p of list) {
    const soon = !!p.comingSoon || p.supported === false;
    const pname = p.name || p.id;
    const btn = el('button', {
      type: 'button',
      className: (state.sourceFilter === p.id ? 'active ' : '') + (soon ? 'soon' : ''),
      disabled: soon,
      title: soon ? t('filter.soonTitle', { name: pname }) : t('filter.providerTitle', { name: pname }),
      onclick: () => {
        if (soon) return;
        state.sourceFilter = p.id;
        renderFilterPills();
        fetchSessions();
      },
    }, el('span', { textContent: `${pname}` }));
    if (soon) btn.append(el('span', { className: 'soon-tag', textContent: t('filter.soon') }));
    host.append(btn);
  }
}

export function renderWorkspaceSelect(workspaces, { fromPoll = false } = {}) {
  const sel = $('cwdSelect');
  const prevVal = state.cwdCustomMode ? '__custom__' : state.cwdFilter;
  $('openSyncDirLabel').textContent = state.cwdFilter ? t('filter.syncThisFolder') : t('filter.syncFolder');

  const sigParts = [getLang(), state.cwdCustomMode ? '__custom__' : normalizePathKey(prevVal)];
  for (const w of workspaces) {
    if (!w || !w.cwd) continue;
    const c = w.counts || {};
    sigParts.push(`${w.cwd}:${w.total ?? 0}:${c.agy ?? 0}:${c.claude ?? 0}`);
  }
  const sig = sigParts.join('|');
  if (fromPoll && document.activeElement === sel) return;
  if (sig === state.lastWorkspaceSelectSig && sel.options.length > 0) return;
  state.lastWorkspaceSelectSig = sig;

  sel.replaceChildren();

  const totalAll = workspaces.reduce((sum, w) => sum + (w.total || 0), 0);
  sel.append(el('option', {
    value: '',
    textContent: totalAll ? t('filter.allFoldersCount', { n: totalAll }) : t('filter.allFolders'),
  }));

  let matched = !prevVal;
  let selectedVal = '';
  for (const w of workspaces) {
    if (!w || !w.cwd) continue;
    const counts = w.counts || {};
    const agyC = counts.agy ?? 0;
    const claudeC = counts.claude ?? 0;
    const opt = el('option', {
      value: w.cwd,
      textContent: `${w.cwd} (${w.total ?? (agyC + claudeC)} \u00b7 agy ${agyC} / claude ${claudeC})`,
    });
    if (normalizePathKey(w.cwd) === normalizePathKey(prevVal)) {
      opt.selected = true;
      matched = true;
      selectedVal = w.cwd;
    }
    sel.append(opt);
  }

  if (prevVal && prevVal !== '__custom__' && !matched) {
    sel.append(el('option', { value: prevVal, textContent: prevVal, selected: true }));
    selectedVal = prevVal;
  }
  const customOpt = el('option', { value: '__custom__', textContent: t('filter.customFolderOpt') });
  if (state.cwdCustomMode) {
    customOpt.selected = true;
    selectedVal = '__custom__';
  }
  sel.append(customOpt);
  sel.value = selectedVal;
}

export function renderStatus(s, { fromPoll = false } = {}) {
  state.status = s;
  $('versionTag').textContent = s.version ? `v${s.version}` : t('top.ready');
  $('autostartToggle').checked = !!s.autostart;

  const providers = Array.isArray(s.providers) && s.providers.length ? s.providers : [
    { id: 'agy', name: 'Antigravity', badge: 'agy', supported: true, comingSoon: false, count: 0 },
    { id: 'claude', name: 'Claude Code', badge: 'claude', supported: true, comingSoon: false, count: 0 },
  ];

  // Topbar provider chips
  const chips = $('providerChips');
  chips.replaceChildren();
  for (const p of providers) {
    const soon = !!p.comingSoon || p.supported === false;
    const pname = p.name || p.id;
    const chip = el('span', {
      className: 'pchip ' + (soon ? 'soon' : 'clickable'),
      title: soon ? t('filter.chipSoonTitle', { name: pname }) : t('filter.chipTitle', { name: pname }),
    });
    chip.append(el('span', { className: 'dot ' + (p.id === 'agy' || p.id === 'claude' ? p.id : '') }));
    chip.append(el('span', { textContent: `${pname}` }));
    if (soon) {
      chip.append(el('span', { className: 'soon-tag', textContent: t('filter.soon') }));
    } else {
      chip.append(el('strong', { textContent: String(p.count ?? 0) }));
      if (p.sizeHuman) {
        chip.append(el('span', { className: 'brand-sub', textContent: `\u00b7 ${p.sizeHuman}` }));
      }
      chip.onclick = () => {
        state.sourceFilter = state.sourceFilter === p.id ? 'all' : p.id;
        switchTab('sessions');
        renderFilterPills();
        fetchSessions();
      };
    }
    chips.append(chip);
  }

  renderFilterPills(providers);
  renderWorkspaceSelect(s.workspaces || [], { fromPoll });
  renderAutosyncStatus(s.autosync || {}, s.workspaces || []);
}
