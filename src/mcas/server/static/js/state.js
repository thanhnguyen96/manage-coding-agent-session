export const state = {
  tab: 'sessions',
  status: null,
  sessions: [],
  groups: [],
  total: 0,
  sourceFilter: 'all',
  cwdFilter: '',
  cwdCustomMode: false,
  q: '',
  includeAll: false,
  sort: 'updated_desc',
  limit: 200,
  sessionsReqSeq: 0,
  filtering: false,
  lastWorkspaceSelectSig: '',
  selected: new Map(), // groupKey -> group
  lastCheckedIdx: -1,
  openDropdownKey: null,
  trimTarget: null,
  autosyncDirty: false,
  watchedDraft: [],
  drawerSession: null, // { source, id, ... }
  drawerGroup: null,
  busy: false,
  connFailures: 0,
  stopped: false,
};

// Expose state on window for DevTools inspection and CDP E2E tests.
if (typeof window !== 'undefined') {
  window.state = state;
}

export function storageGet(k, fallback = '') {
  try {
    return localStorage.getItem(k) ?? fallback;
  } catch {
    return fallback;
  }
}

export function storageSet(k, v) {
  try {
    localStorage.setItem(k, v);
  } catch {}
}
