import { getLang, t } from '../i18n.js';

export const PROVIDER_ORDER = ['claude', 'agy'];

export function parseDateVal(v) {
  if (v === undefined || v === null || v === '') return null;
  if (typeof v === 'number') return new Date(v < 1e12 ? v * 1000 : v);
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatRelative(v, fallback) {
  const d = parseDateVal(v);
  if (!d) return fallback || '';
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 45) return t('time.justNow');
  if (sec < 3600) return t('time.mAgo', { n: Math.floor(sec / 60) });
  if (sec < 86400) return t('time.hAgo', { n: Math.floor(sec / 3600) });
  if (sec < 86400 * 7) return t('time.dAgo', { n: Math.floor(sec / 86400) });
  return d.toLocaleDateString(getLang() === 'vi' ? 'vi-VN' : undefined);
}

export function formatLocalDate(v, fallback = '') {
  const d = parseDateVal(v);
  if (!d) return fallback;
  return d.toLocaleString(getLang() === 'vi' ? 'vi-VN' : undefined);
}

export function providerLabel(id) {
  if (id === 'agy') return 'Antigravity';
  if (id === 'claude') return 'Claude Code';
  if (id === 'opencode') return 'OpenCode';
  if (id === 'codex') return 'Codex';
  return id || 'unknown';
}

export function oppositeProvider(id) {
  return id === 'agy' ? 'claude' : 'agy';
}

export function sessionKey(s) {
  return `${s.source}:${s.id}`;
}

export function groupKey(g) {
  return g.groupKey || (g.primary ? sessionKey(g.primary) : '');
}

export function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${Math.max(0, Math.round(n))} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function orderedProvidersList(list) {
  const uniq = [...new Set(list)];
  uniq.sort((a, b) => {
    const ia = PROVIDER_ORDER.indexOf(a);
    const ib = PROVIDER_ORDER.indexOf(b);
    if (ia !== -1 && ib !== -1) return ia - ib;
    if (ia !== -1) return -1;
    if (ib !== -1) return 1;
    return a.localeCompare(b);
  });
  return uniq;
}

export function normalizePathKey(p) {
  return (p || '').trim().replace(/[\\/]+$/, '').toLowerCase();
}

export function formatSyncedInfo(syncedTo) {
  if (!syncedTo) return { text: '', title: '' };
  if (Array.isArray(syncedTo) && syncedTo.length) {
    const names = [];
    const tips = [];
    for (const item of syncedTo) {
      if (typeof item === 'string') {
        names.push(item);
        tips.push(item);
      } else if (item && typeof item === 'object') {
        const prov = item.provider || item.target || item.source || 'synced';
        names.push(prov);
        const parts = [prov];
        if (item.id) parts.push(item.id);
        if (item.resumeCmd) parts.push(`(${item.resumeCmd})`);
        tips.push(parts.join(' '));
      }
    }
    if (names.length) return { text: t('sync.label', { names: [...new Set(names)].join(', ') }), title: tips.join('\n') };
  }
  if (typeof syncedTo === 'string' && syncedTo.trim()) {
    return { text: t('sync.label', { names: syncedTo }), title: syncedTo };
  }
  return { text: '', title: '' };
}

export function formatExtra(extra) {
  if (!extra) return '';
  if (typeof extra === 'string') return extra;
  if (typeof extra === 'object') return Object.keys(extra).filter((k) => extra[k]).join(', ');
  return String(extra);
}

export function formatResumeCmd(provider, id) {
  if (!id) return '';
  if (provider === 'claude') return `claude --resume ${id}`;
  if (provider === 'agy') return `agy --conversation ${id}`;
  return '';
}
