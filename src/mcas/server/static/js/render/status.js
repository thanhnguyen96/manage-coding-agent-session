import { isAdvanced, state, storageSet } from '../state.js';
import { $, el } from '../utils/dom.js';
import {
  formatWorkspaceLabel,
  normalizePathKey,
  providerDotClass,
  supportedProviderIds,
} from '../utils/format.js';
import { renderAutosyncStatus } from '../panels/autosync.js';
import { fetchSessions } from '../actions.js';
import { getLang, t } from '../i18n.js';

const FALLBACK_PROVIDERS = [
  { id: 'agy', name: 'Antigravity', badge: 'agy', supported: true, comingSoon: false, count: 0 },
  { id: 'claude', name: 'Claude Code', badge: 'claude', supported: true, comingSoon: false, count: 0 },
  { id: 'codex', name: 'Codex', badge: 'codex', supported: true, comingSoon: false, count: 0 },
];

export function updateSyncDirButton() {
  const btn = $('openSyncDirBtn');
  if (!btn) return;
  btn.hidden = !isAdvanced() && !state.cwdFilter;
  $('openSyncDirLabel').textContent = state.cwdFilter ? t('filter.syncThisFolder') : t('filter.syncFolder');
}

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
  const list = providers || (state.status && state.status.providers) || FALLBACK_PROVIDERS;
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
    if (soon) continue;
    const pname = p.name || p.id;
    const btn = el('button', {
      type: 'button',
      className: state.sourceFilter === p.id ? 'active' : '',
      title: t('filter.providerTitle', { name: pname }),
      onclick: () => {
        state.sourceFilter = p.id;
        renderFilterPills();
        fetchSessions();
      },
    }, el('span', { textContent: `${pname}` }));
    host.append(btn);
  }
}

export function renderWorkspaceSelect(workspaces, { fromPoll = false } = {}) {
  const sel = $('cwdSelect');
  const adv = isAdvanced();
  const prevVal = (adv && state.cwdCustomMode) ? '__custom__' : state.cwdFilter;
  updateSyncDirButton();

  const activeIds = supportedProviderIds(state.status && state.status.providers);
  const sigParts = [
    getLang(),
    adv ? 'adv' : 'basic',
    (adv && state.cwdCustomMode) ? '__custom__' : normalizePathKey(prevVal),
    activeIds.join(','),
  ];
  for (const w of workspaces) {
    if (!w || !w.cwd) continue;
    const c = w.counts || {};
    const countsSig = activeIds.map((id) => c[id] ?? 0).join(':');
    sigParts.push(`${w.cwd}:${w.total ?? 0}:${countsSig}`);
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
    const opt = el('option', {
      value: w.cwd,
      textContent: formatWorkspaceLabel(w, activeIds),
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
  if (adv) {
    const customOpt = el('option', { value: '__custom__', textContent: t('filter.customFolderOpt') });
    if (state.cwdCustomMode) {
      customOpt.selected = true;
      selectedVal = '__custom__';
    }
    sel.append(customOpt);
  }
  sel.value = selectedVal;
}

export function renderStatus(s, { fromPoll = false } = {}) {
  state.status = s;
  $('versionTag').textContent = s.version ? `v${s.version}` : t('top.ready');
  $('autostartToggle').checked = !!s.autostart;

  const providers = Array.isArray(s.providers) && s.providers.length ? s.providers : FALLBACK_PROVIDERS;

  // Topbar provider chips (active providers only to keep header uncluttered)
  const chips = $('providerChips');
  chips.replaceChildren();
  for (const p of providers) {
    const soon = !!p.comingSoon || p.supported === false;
    if (soon) continue;
    const pname = p.name || p.id;
    const dotCls = providerDotClass(p.id);
    const chip = el('span', {
      className: 'pchip clickable',
      title: t('filter.chipTitle', { name: pname }),
    });
    chip.append(el('span', { className: 'dot' + (dotCls ? ' ' + dotCls : '') }));
    chip.append(el('span', { textContent: `${pname}` }));
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
    chips.append(chip);
  }

  renderFilterPills(providers);
  renderWorkspaceSelect(s.workspaces || [], { fromPoll });
  renderAutosyncStatus(s.autosync || {}, s.workspaces || []);
}
