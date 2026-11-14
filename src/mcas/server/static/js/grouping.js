import { state } from './state.js';
import {
  formatBytes,
  formatResumeCmd,
  orderedProvidersList,
  sessionKey,
} from './utils/format.js';

export function sortGroups(groups, sortKey) {
  const k = sortKey || 'updated_desc';
  groups.sort((a, b) => {
    if (k === 'size_desc') return (b.sizeBytes - a.sizeBytes) || (b.updatedMs - a.updatedMs);
    if (k === 'turns_desc') return (b.count - a.count) || (b.updatedMs - a.updatedMs);
    if (k === 'updated_asc') return a.updatedMs - b.updatedMs;
    return b.updatedMs - a.updatedMs;
  });
}

export function groupSessions(rawSessions) {
  const nodes = new Map();
  const adj = new Map();
  const addEdge = (a, b) => {
    if (!adj.has(a)) adj.set(a, new Set());
    if (!adj.has(b)) adj.set(b, new Set());
    adj.get(a).add(b);
    adj.get(b).add(a);
  };

  for (const s of rawSessions) {
    if (!s || !s.source || !s.id) continue;
    const k = sessionKey(s);
    nodes.set(k, {
      id: s.id,
      source: s.source,
      title: s.title || '',
      preview: s.preview || '',
      cwd: s.cwd || '',
      updatedAt: s.updatedAt || '',
      updatedLocal: s.updatedLocal || '',
      updatedMs: Date.parse(s.updatedAt || '') || 0,
      count: Number(s.count) || 0,
      sizeBytes: Number(s.sizeBytes) || 0,
      sizeHuman: s.sizeHuman || formatBytes(s.sizeBytes || 0),
      extra: s.extra || '',
      resumeCmd: s.resumeCmd || formatResumeCmd(s.source, s.id),
      resumeDir: s.resumeDir || s.cwd || '',
      syncedTo: Array.isArray(s.syncedTo) ? s.syncedTo : [],
    });
  }

  for (const s of rawSessions) {
    if (!s || !s.source || !s.id || !Array.isArray(s.syncedTo)) continue;
    const sk = sessionKey(s);
    for (const peer of s.syncedTo) {
      if (!peer || typeof peer !== 'object') continue;
      const pprov = peer.provider || peer.target || peer.source || '';
      const pid = peer.id || '';
      if (!pprov || !pid) continue;
      const pk = `${pprov}:${pid}`;
      if (!nodes.has(pk)) {
        nodes.set(pk, {
          id: pid,
          source: pprov,
          title: peer.title || s.title || '',
          preview: peer.preview || s.preview || '',
          cwd: peer.cwd || s.cwd || '',
          updatedAt: peer.updatedAt || s.updatedAt || '',
          updatedLocal: peer.updatedLocal || s.updatedLocal || '',
          updatedMs: Date.parse(peer.updatedAt || s.updatedAt || '') || 0,
          count: peer.count !== undefined ? Number(peer.count) : (Number(s.count) || 0),
          sizeBytes: Number(peer.sizeBytes) || 0,
          sizeHuman: peer.sizeHuman || formatBytes(peer.sizeBytes || 0),
          extra: peer.extra || '',
          resumeCmd: peer.resumeCmd || formatResumeCmd(pprov, pid),
          resumeDir: peer.resumeDir || peer.cwd || s.cwd || '',
          syncedTo: [],
        });
      }
      addEdge(sk, pk);
    }
  }

  const visited = new Set();
  const groups = [];
  for (const startKey of nodes.keys()) {
    if (visited.has(startKey)) continue;
    const queue = [startKey];
    visited.add(startKey);
    const compKeys = [];
    while (queue.length) {
      const cur = queue.shift();
      compKeys.push(cur);
      const neighbors = adj.get(cur);
      if (neighbors) {
        for (const nxt of neighbors) {
          if (!visited.has(nxt) && nodes.has(nxt)) {
            visited.add(nxt);
            queue.push(nxt);
          }
        }
      }
    }
    const comp = compKeys.map((k) => nodes.get(k));
    comp.sort((a, b) => b.updatedMs - a.updatedMs);
    const primary = comp[0];
    const members = {};
    const membersByProv = {};
    for (const m of comp) {
      if (!members[m.source]) members[m.source] = m;
      if (!membersByProv[m.source]) membersByProv[m.source] = [];
      membersByProv[m.source].push(m);
    }
    const providers = orderedProvidersList(Object.keys(members));
    const totalSize = comp.reduce((acc, m) => acc + (Number(m.sizeBytes) || 0), 0);
    const maxCount = comp.reduce((acc, m) => Math.max(acc, Number(m.count) || 0), 0);
    const firstTitle = (comp.find((m) => m.title) || {}).title || '';
    const firstPreview = (comp.find((m) => m.preview) || {}).preview || '';
    const firstCwd = (comp.find((m) => m.cwd) || {}).cwd || '';
    const gk = [...compKeys].sort().join('|');

    const groupObj = {
      groupKey: gk,
      primary,
      members,
      membersByProv,
      membersList: comp,
      providers,
      title: primary.title || firstTitle,
      preview: primary.preview || firstPreview,
      cwd: primary.cwd || firstCwd,
      updatedAt: primary.updatedAt,
      updatedLocal: primary.updatedLocal,
      updatedMs: primary.updatedMs,
      count: maxCount,
      sizeBytes: totalSize,
      sizeHuman: formatBytes(totalSize),
      extra: primary.extra || '',
    };
    if (state.selected.has(gk)) state.selected.set(gk, groupObj);
    groups.push(groupObj);
  }

  sortGroups(groups, state.sort);
  return groups;
}
