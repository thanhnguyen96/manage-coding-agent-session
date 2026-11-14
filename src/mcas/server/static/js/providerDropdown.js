import { state } from './state.js';
import { el } from './utils/dom.js';
import { providerLabel } from './utils/format.js';
import { renderSessions } from './render/sessionList.js';
import { applyGroupProviders } from './actions.js';
import { t } from './i18n.js';

export function closeProviderDropdown(shouldRender = true) {
  if (!state.openDropdownKey) return;
  state.openDropdownKey = null;
  if (shouldRender) renderSessions();
}

export function buildProviderTagButton(g, onToggle) {
  const btn = el('button', {
    type: 'button',
    className: 'prov-tag-btn',
    title: t('dd.tagTitle'),
    'data-dropdown-host': 'true',
    onclick: (e) => {
      e.stopPropagation();
      onToggle();
    },
  });
  for (const p of g.providers) {
    btn.append(el('span', { className: 'prov-tag-dot ' + (p === 'agy' ? 'agy' : p === 'claude' ? 'claude' : '') }));
  }
  btn.append(el('span', { textContent: `(${g.providers.join(', ')})` }));
  return btn;
}

export function buildProviderDropdownMenu(g) {
  const menu = el('div', {
    className: 'prov-dropdown-menu',
    onclick: (e) => e.stopPropagation(),
  });
  menu.append(el('div', { className: 'prov-dd-title', textContent: t('dd.title') }));

  const supportedIds = ['claude', 'agy'];
  const cbMap = {};
  const badgeMap = {};

  for (const pid of supportedIds) {
    const present = !!g.members[pid];
    const cb = el('input', { type: 'checkbox', checked: present });
    cbMap[pid] = cb;
    const badge = el('span', {
      className: 'prov-dd-badge',
      textContent: present ? (g.members[pid].sizeHuman || t('dd.synced')) : t('dd.notHere'),
    });
    badgeMap[pid] = badge;

    const rowLabel = el('label', { className: 'prov-dd-row' },
      el('span', { className: 'prov-dd-left' },
        cb,
        el('span', { className: 'prov-tag-dot ' + pid }),
        el('span', { textContent: `${providerLabel(pid)} (${pid})` })
      ),
      badge
    );
    menu.append(rowLabel);
  }

  for (const soonId of ['opencode', 'codex']) {
    menu.append(el('div', { className: 'prov-dd-row disabled' },
      el('span', { className: 'prov-dd-left' },
        el('input', { type: 'checkbox', disabled: true }),
        el('span', { textContent: `${providerLabel(soonId)} (${soonId})` })
      ),
      el('span', { className: 'prov-dd-badge', textContent: t('filter.soon') })
    ));
  }

  const thoughtCb = el('input', { type: 'checkbox' });
  const optsWrap = el('div', { className: 'prov-dd-opts', hidden: true },
    el('label', { className: 'check' }, thoughtCb, el('span', { textContent: t('dd.includeThought') }))
  );
  menu.append(optsWrap);

  const warnBox = el('div', {
    className: 'prov-dd-warn',
    hidden: true,
    textContent: t('dd.warnDeleteAll'),
  });
  menu.append(warnBox);

  const untickBtn = el('button', {
    type: 'button',
    className: 'sm ghost',
    textContent: t('dd.untickAll'),
  });
  const applyBtn = el('button', {
    type: 'button',
    className: 'sm primary',
    textContent: t('dd.apply'),
    disabled: true,
  });
  menu.append(el('div', { className: 'prov-dd-foot' }, untickBtn, applyBtn));

  const syncMenuState = () => {
    const desired = supportedIds.filter((pid) => cbMap[pid].checked);
    let hasAdd = false;
    let changed = false;
    for (const pid of supportedIds) {
      const was = !!g.members[pid];
      const now = cbMap[pid].checked;
      const b = badgeMap[pid];
      if (!was && now) {
        hasAdd = true;
        changed = true;
        b.className = 'prov-dd-badge add';
        b.textContent = t('dd.add');
      } else if (was && !now) {
        changed = true;
        b.className = 'prov-dd-badge remove';
        b.textContent = t('dd.remove');
      } else if (was && now) {
        b.className = 'prov-dd-badge';
        b.textContent = g.members[pid].sizeHuman || t('dd.present');
      } else {
        b.className = 'prov-dd-badge';
        b.textContent = t('dd.notHere');
      }
    }
    optsWrap.hidden = !hasAdd;
    warnBox.hidden = desired.length > 0;
    applyBtn.disabled = !changed;
    if (desired.length === 0) {
      applyBtn.className = 'sm danger';
      applyBtn.textContent = t('dd.applyDelete');
    } else {
      applyBtn.className = 'sm primary';
      applyBtn.textContent = t('dd.apply');
    }
  };

  for (const pid of supportedIds) cbMap[pid].onchange = syncMenuState;
  untickBtn.onclick = () => {
    for (const pid of supportedIds) cbMap[pid].checked = false;
    syncMenuState();
  };
  applyBtn.onclick = () => {
    const desired = supportedIds.filter((pid) => cbMap[pid].checked);
    applyGroupProviders(g, desired, thoughtCb.checked);
  };
  syncMenuState();
  return menu;
}
