import { isAdvanced, state } from './state.js';
import { api } from './api.js';
import { $, el, renderMessageText } from './utils/dom.js';
import {
  formatBytes,
  formatExtra,
  formatLocalDate,
  formatResumeCmd,
  orderedProvidersList,
  otherProviders,
  providerBadgeClass,
  providerLabel,
} from './utils/format.js';
import { copyText } from './components/toast.js';
import { confirmDeleteGroup, copySingleSession } from './actions.js';
import { openTrimModal } from './modals/trimModal.js';
import { t } from './i18n.js';

export function closePreview() {
  $('previewBackdrop').hidden = true;
  state.drawerSession = null;
  state.drawerGroup = null;
}

export function buildFallbackGroupForSession(data) {
  const selfNode = {
    id: data.id,
    source: data.source,
    title: data.title || '',
    preview: data.preview || '',
    cwd: data.cwd || '',
    updatedAt: data.updatedAt || '',
    updatedLocal: data.updatedLocal || '',
    count: Array.isArray(data.turns) ? data.turns.length : (data.count || 0),
    sizeBytes: Number(data.sizeBytes) || 0,
    sizeHuman: data.sizeHuman || formatBytes(data.sizeBytes || 0),
  };
  const members = { [data.source]: selfNode };
  const membersList = [selfNode];
  for (const peer of (Array.isArray(data.syncedTo) ? data.syncedTo : [])) {
    if (!peer || typeof peer !== 'object' || !peer.provider || !peer.id) continue;
    const pNode = {
      id: peer.id,
      source: peer.provider,
      title: peer.title || data.title || '',
      cwd: peer.cwd || data.cwd || '',
      sizeBytes: Number(peer.sizeBytes) || 0,
      sizeHuman: peer.sizeHuman || formatBytes(peer.sizeBytes || 0),
    };
    if (!members[pNode.source]) members[pNode.source] = pNode;
    membersList.push(pNode);
  }
  const providers = orderedProvidersList(Object.keys(members));
  const totalBytes = membersList.reduce((acc, m) => acc + (Number(m.sizeBytes) || 0), 0);
  return {
    groupKey: membersList.map((m) => `${m.source}:${m.id}`).sort().join('|'),
    primary: selfNode,
    members,
    membersList,
    providers,
    title: data.title || '',
    cwd: data.cwd || '',
    count: selfNode.count,
    sizeBytes: totalBytes,
    sizeHuman: formatBytes(totalBytes),
  };
}

export function renderDrawerContent(data) {
  state.drawerSession = data;
  const matchedGroup = state.groups.find((g) =>
    g.membersList.some((m) => m.source === data.source && m.id === data.id)
  ) || state.drawerGroup || buildFallbackGroupForSession(data);
  state.drawerGroup = matchedGroup;

  const badge = $('drawerBadge');
  badge.textContent = data.source || 'session';
  badge.className = 'pbadge ' + providerBadgeClass(data.source);
  $('drawerTitle').textContent = data.title || t('row.untitled');

  const tabsHost = $('drawerProvTabs');
  if (matchedGroup.providers.length > 1) {
    tabsHost.hidden = false;
    tabsHost.replaceChildren();
    for (const p of matchedGroup.providers) {
      const m = matchedGroup.members[p];
      if (!m) continue;
      tabsHost.append(el('button', {
        type: 'button',
        className: 'drawer-prov-tab' + (p === data.source && m.id === data.id ? ' active' : ''),
        textContent: `${p} (${m.sizeHuman || formatBytes(m.sizeBytes || 0)})`,
        onclick: () => openPreview(m.source, m.id, matchedGroup),
      }));
    }
  } else {
    tabsHost.hidden = true;
    tabsHost.replaceChildren();
  }

  const extra = formatExtra(data.extra);
  $('drawerExtra').hidden = !extra;
  $('drawerExtra').textContent = extra;

  const meta = $('drawerMetaGrid');
  const sizeDetail = data.sizeHuman
    ? t('drawer.sizeDetail', { total: data.sizeHuman, main: data.mainHuman || '0 B', sidecar: data.sidecarHuman || '0 B' })
    : '0 B';
  meta.replaceChildren(
    el('div', {}, el('span', { className: 'brand-sub', textContent: t('drawer.idLbl') }), el('code', { textContent: data.id || '', title: data.id || '' })),
    el('div', {}, el('span', { className: 'brand-sub', textContent: t('drawer.folderLbl') }), el('code', { textContent: data.cwd || t('drawer.none'), title: data.cwd || '' })),
    el('div', {}, el('span', { className: 'brand-sub', textContent: t('drawer.updatedLbl') }), el('span', { textContent: data.updatedLocal || formatLocalDate(data.updatedAt) || '-' })),
    el('div', {}, el('span', { className: 'brand-sub', textContent: t('drawer.turnsLbl') }), el('span', { textContent: String((data.turns || []).length) })),
    el('div', {}, el('span', { className: 'brand-sub', textContent: t('drawer.sizeLbl') }), el('span', { textContent: sizeDetail }))
  );
  if (matchedGroup.providers.length > 1) {
    meta.append(el('div', {},
      el('span', { className: 'brand-sub', textContent: t('drawer.providersLbl') }),
      el('span', { className: 'sync-badge', textContent: `(${matchedGroup.providers.join(', ')})` })
    ));
  }

  const rDir = data.resumeDir || data.cwd || '';
  $('drawerDirBox').hidden = !rDir;
  $('drawerDirText').textContent = rDir ? `cd "${rDir}"` : '';
  $('drawerDirText').title = rDir ? `cd "${rDir}"` : '';

  const rCmd = data.resumeCmd || formatResumeCmd(data.source, data.id);
  $('drawerCmdBox').hidden = !rCmd;
  $('drawerCmdText').textContent = rCmd;
  $('drawerCmdText').title = rCmd;

  const targets = otherProviders(data.source, state.status && state.status.providers);
  const targetSel = $('drawerCopyTarget');
  targetSel.replaceChildren();
  for (const tid of targets) {
    targetSel.append(el('option', { value: tid, textContent: `${providerLabel(tid)} (${tid})` }));
  }
  const missingTarget = targets.find((tid) => !(matchedGroup.members && matchedGroup.members[tid]));
  targetSel.value = missingTarget || targets[0] || '';
  $('drawerCopyBtn').textContent = t('drawer.copyToBtn');
  $('drawerCopyBtn').disabled = targets.length === 0;
  $('drawerCopyBtn').onclick = () => copySingleSession(data.source, data.id, targetSel.value, isAdvanced() && $('drawerThinking').checked);
  $('drawerTrimBtn').onclick = () => openTrimModal(matchedGroup);
  $('drawerDeleteBtn').onclick = () => confirmDeleteGroup(matchedGroup);

  const turnsHost = $('drawerTurns');
  turnsHost.replaceChildren();
  const turns = Array.isArray(data.turns) ? data.turns : [];
  if (!turns.length) {
    turnsHost.append(el('div', { className: 'empty-state', textContent: t('drawer.emptyTurns') }));
    return;
  }

  for (const turn of turns) {
    const role = (turn.role || 'assistant').toLowerCase();
    const cls = (role === 'user' || role === 'assistant' || role === 'tool_result' || role === 'error') ? role : 'system';
    const card = el('article', { className: 'turn ' + cls });

    const head = el('div', { className: 'turn-head' },
      el('div', { className: 'row-title-line' },
        el('span', { className: 'role-badge', textContent: role.replace('_', ' ') }),
        el('span', { textContent: `#${(turn.index ?? 0) + 1}` })
      ),
      el('span', { textContent: turn.timestampLocal || (turn.timestamp ? formatLocalDate(turn.timestamp, turn.timestamp) : '') })
    );
    card.append(head);

    if (turn.thinking && String(turn.thinking).trim()) {
      const th = String(turn.thinking);
      card.append(el('details', { className: 'thought' },
        el('summary', { textContent: t('drawer.thinkingChars', { n: th.length }) }),
        el('pre', { textContent: th })
      ));
    }

    if (turn.text && String(turn.text).trim()) {
      const txtEl = el('div', { className: 'turn-text' });
      renderMessageText(txtEl, turn.text);
      card.append(txtEl);
    }

    if (Array.isArray(turn.toolCalls)) {
      for (const tc of turn.toolCalls) {
        if (!tc) continue;
        const name = tc.name || 'tool';
        const idPart = tc.id ? ` (${tc.id})` : '';
        let args = tc.arguments ?? tc.input ?? tc.arguments_json ?? '';
        if (typeof args === 'string') {
          try { args = JSON.stringify(JSON.parse(args), null, 2); } catch {}
        } else if (typeof args === 'object') {
          args = JSON.stringify(args, null, 2);
        }
        card.append(el('details', {},
          el('summary', { textContent: t('drawer.toolCall', { name, id: idPart }) }),
          el('pre', { textContent: String(args || '{}') })
        ));
      }
    }

    if (Array.isArray(turn.toolResults)) {
      for (const tr of turn.toolResults) {
        if (!tr) continue;
        const isErr = !!(tr.isError ?? tr.is_error);
        const callId = tr.toolUseId || tr.toolCallId || tr.tool_use_id || '';
        const name = tr.name || callId || 'result';
        const idSuffix = (tr.name && callId) ? ` (${callId})` : '';
        const out = typeof tr.content === 'string' ? tr.content : JSON.stringify(tr.content ?? tr.output ?? '', null, 2);
        const det = el('details', {},
          el('summary', {
            textContent: t('drawer.toolResult', { err: isErr ? ' [ERROR]' : '', name, id: idSuffix }),
            style: isErr ? { color: 'var(--danger)' } : undefined,
          }),
          el('pre', { textContent: out || t('drawer.emptyOutput') })
        );
        card.append(det);
      }
    }

    turnsHost.append(card);
  }
}

export function openPreviewGroup(g, preferredProv) {
  const m = (preferredProv && g.members && g.members[preferredProv]) || g.primary;
  if (!m) return;
  return openPreview(m.source, m.id, g);
}

export async function openPreview(source, id, group = null) {
  if (group) state.drawerGroup = group;
  $('previewBackdrop').hidden = false;
  $('drawerTitle').textContent = t('drawer.loading');
  $('drawerProvTabs').hidden = true;
  $('drawerMetaGrid').replaceChildren();
  $('drawerDirBox').hidden = true;
  $('drawerCmdBox').hidden = true;
  $('drawerTurns').replaceChildren(el('div', { className: 'empty-state', textContent: t('drawer.loadingTurns') }));

  try {
    const params = new URLSearchParams({ source, id });
    const data = await api('session?' + params.toString());
    renderDrawerContent(data);
  } catch (e) {
    $('drawerTitle').textContent = t('drawer.loadFailed');
    $('drawerTurns').replaceChildren(el('div', { className: 'empty-state', textContent: e.message }));
  }
}

export function initDrawer() {
  $('drawerCloseBtn').onclick = closePreview;
  $('previewBackdrop').onclick = (e) => { if (e.target === $('previewBackdrop')) closePreview(); };
  $('drawerCopyDir').onclick = () => copyText($('drawerDirText').textContent, $('drawerCopyDir'));
  $('drawerCopyCmd').onclick = () => copyText($('drawerCmdText').textContent, $('drawerCopyCmd'));
}
