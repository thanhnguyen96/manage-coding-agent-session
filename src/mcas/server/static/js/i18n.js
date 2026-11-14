import { storageGet, storageSet } from './state.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const listeners = new Set();

function normalizeLang(lang) {
  return lang === 'vi' ? 'vi' : 'en';
}

let currentLang = normalizeLang(storageGet('mcas.lang', 'en'));

export const DICT = {
  en: {
    'page.title': 'mcas \u2014 Manage Coding Agent Session',
    'brand.sub': 'Manage Coding Agent Session',
    'top.connecting': 'connecting...',
    'top.ready': 'ready',
    'top.autosyncTitle': 'Open Auto-Sync settings & activity',
    'top.autostart': 'Start with Windows',
    'top.autostartTitle': 'Launch mcas tray icon automatically on Windows sign-in',
    'top.autostartEnabled': 'Start with Windows enabled.',
    'top.autostartDisabled': 'Start with Windows disabled.',
    'top.refresh': 'Refresh',
    'top.refreshTitle': 'Refresh status and sessions',
    'top.stopServer': 'Stop server',
    'top.stopServerTitle': 'Stop the mcas server and remove the tray icon',
    'top.help': 'Help',
    'top.helpTitle': 'Open Visual Guide & FAQ (?)',
    'top.langTitle': 'Switch language',
    'top.themeToLight': 'Switch to light mode',
    'top.themeToDark': 'Switch to dark mode',
    'top.themeAria': 'Toggle light/dark theme',

    'conn.lost': 'Connection lost.',
    'conn.unreachable': 'UI server unreachable. Start it again with "mcas ui".',
    'conn.reconnect': 'Reconnect',
    'conn.loadFailed': 'Failed to load sessions',
    'conn.loadFailedToast': 'Failed to load sessions.',

    'quit.title': 'Stop mcas server',
    'quit.msg': 'Stop the mcas web UI server, background Auto-Sync, and tray icon? Run "mcas ui" again to reopen.',
    'quit.stoppedTitle': 'mcas server stopped',
    'quit.stoppedDesc': 'The web server and tray icon have shut down. You can close this tab or run "mcas ui" to start again.',

    'tabs.aria': 'Primary views',
    'tabs.sessions': 'Sessions & Copy/Sync',
    'tabs.autosync': 'Auto-Sync & Activity',
    'tabs.badgeSync': 'SYNC',
    'tabs.badgeOn': 'ON',
    'tabs.badgeOff': 'OFF',

    'filter.providerAria': 'Filter by provider',
    'filter.all': 'All',
    'filter.soon': 'Soon',
    'filter.soonTitle': '{name} (Coming soon)',
    'filter.providerTitle': 'Show {name} sessions',
    'filter.chipTitle': 'Filter to {name} sessions',
    'filter.chipSoonTitle': '{name} support coming soon',
    'filter.folderAria': 'Filter by workspace folder',
    'filter.allFolders': 'All folders',
    'filter.allFoldersCount': 'All folders ({n})',
    'filter.customFolderOpt': 'Custom folder path...',
    'filter.customFolderPh': 'Enter folder path (e.g. C:\\repo)',
    'filter.apply': 'Apply',
    'filter.clear': 'Clear',
    'filter.clearCustomTitle': 'Clear custom folder',
    'filter.searchPh': 'Search title, preview, ID, folder...',
    'filter.searchClearTitle': 'Clear search',
    'filter.includeAll': 'Include subagents / IDE / empty',
    'filter.sortAria': 'Sort conversations',
    'filter.sortNewest': 'Sort: Newest',
    'filter.sortLargest': 'Sort: Largest size',
    'filter.sortTurns': 'Sort: Most turns',
    'filter.sortOldest': 'Sort: Oldest',
    'filter.cleanUp': 'Clean Up...',
    'filter.syncFolder': 'Sync folder \u2192 ...',
    'filter.syncThisFolder': 'Sync this folder \u2192 ...',
    'filter.clearChipTitle': 'Clear filter',
    'filter.chipProvider': 'Provider: {val}',
    'filter.chipFolder': 'Folder: {val}',
    'filter.chipSearch': 'Search: "{val}"',

    'sel.zero': '0 selected',
    'sel.countBytes': '{n} selected \u00b7 {size}',
    'sel.clear': 'Clear',
    'sel.to': 'To',
    'sel.targetAria': 'Target provider for selected sessions',
    'sel.targetCwdPh': 'Target folder override (blank = keep session folder)',
    'sel.includeThought': 'Include <thought>',
    'sel.newId': 'Force new UUID',
    'sel.overwrite': 'Overwrite',
    'sel.copySelected': 'Copy Selected',
    'sel.deleteSelected': 'Delete Selected',
    'sel.selectAll': 'Select all visible',

    'list.loading': 'Loading sessions...',
    'list.loadingConvs': 'Loading conversations...',
    'list.countOne': '{n} conversation',
    'list.countMany': '{n} conversations',
    'list.countGroupedOne': '{n} conversation ({raw} entries)',
    'list.countGroupedMany': '{n} conversations ({raw} entries)',
    'list.emptyFiltered': 'No conversations match the current filters.',
    'list.emptyNone': 'No conversations found yet.',
    'list.showAllProviders': 'Show all providers in this folder',
    'list.resetFilters': 'Reset filters',
    'list.footShowing': 'Showing {shown} raw entries ({grouped} grouped) of {total} matching entries',
    'list.loadMore': 'Load more',

    'row.untitled': '(untitled)',
    'row.selectAria': 'Select {title}',
    'row.idTipSingle': 'Click to copy full ID: {id}',
    'row.filterFolderTip': 'Filter to folder: {cwd}',
    'row.noWorkspace': '(no workspace)',
    'row.turns': '{n} turns',
    'row.providersBtn': 'Providers ({list}) \u25be',
    'row.lighten': 'Lighten',
    'row.lightenTitle': 'Lighten conversation in-place to save space',
    'row.preview': 'Preview',

    'dd.tagTitle': 'Manage which providers hold this conversation',
    'dd.title': 'Providers keeping this chat',
    'dd.synced': 'synced',
    'dd.present': 'present',
    'dd.notHere': 'not here',
    'dd.add': '+ Add',
    'dd.remove': '- Remove',
    'dd.includeThought': 'Include <thought> blocks when syncing',
    'dd.warnDeleteAll': 'All providers unchecked \u2014 clicking Apply will confirm permanently deleting this conversation.',
    'dd.untickAll': 'Untick all',
    'dd.apply': 'Apply',
    'dd.applyDelete': 'Apply (Delete)',

    'copy.copiedTo': 'Copied to {to}',
    'copy.copiedBatch': 'Copied {n} conversation(s) to {to}',
    'copy.addedTo': 'Added to {to}',
    'copy.removedFrom': 'Removed from {list} ({freed} freed)',
    'copy.providersUpdated': 'Providers updated.',

    'delete.confirmTitle': 'Delete conversation completely?',
    'delete.confirmMsg': 'Permanently delete "{title}" from {providers} ({count} {copyWord}, {size})? This cannot be undone.',
    'delete.copyOne': 'copy',
    'delete.copyMany': 'copies',
    'delete.confirmBtn': 'Delete completely',
    'delete.done': 'Conversation deleted.',
    'delete.bulkTitle': 'Delete {n} selected conversation(s)?',
    'delete.bulkMsg': 'Permanently delete {n} conversation(s) ({copies} provider copies, ~{size})? This cannot be undone.',
    'delete.bulkDone': 'Selected conversations deleted.',

    'drawer.defaultTitle': 'Session preview',
    'drawer.close': 'Close',
    'drawer.closeAria': 'Close preview',
    'drawer.resumeDir': 'Resume Dir',
    'drawer.resumeCmd': 'Resume Cmd',
    'drawer.copy': 'Copy',
    'drawer.copyCmd': 'Copy cmd',
    'drawer.copyToClaude': 'Copy \u2192 Claude',
    'drawer.copyToAgy': 'Copy \u2192 AGY',
    'drawer.includeThought': 'Include <thought>',
    'drawer.trim': 'Lighten / Trim',
    'drawer.delete': 'Delete Completely',
    'drawer.loading': 'Loading session...',
    'drawer.loadingTurns': 'Loading conversation turns...',
    'drawer.loadFailed': 'Failed to load session',
    'drawer.emptyTurns': 'This session has no visible conversation turns.',
    'drawer.idLbl': 'ID: ',
    'drawer.folderLbl': 'Folder: ',
    'drawer.none': '(none)',
    'drawer.updatedLbl': 'Updated: ',
    'drawer.turnsLbl': 'Turns: ',
    'drawer.sizeLbl': 'Size: ',
    'drawer.sizeDetail': '{total} (main: {main}, sidecars: {sidecar})',
    'drawer.providersLbl': 'Providers: ',
    'drawer.thinkingChars': 'Thinking ({n} chars)',
    'drawer.toolCall': 'Tool call: {name}{id}',
    'drawer.toolResult': 'Tool result{err}: {name}{id}',
    'drawer.emptyOutput': '(empty output)',

    'syncDir.title': 'Sync Folder Sessions',
    'syncDir.sourceLabel': 'Source Workspace Folder',
    'syncDir.customPh': 'Enter folder path (e.g. C:\\_personal\\_empty)',
    'syncDir.directionLabel': 'Direction',
    'syncDir.dirAgyToClaude': 'Antigravity (agy) \u2192 Claude Code (claude)',
    'syncDir.dirClaudeToAgy': 'Claude Code (claude) \u2192 Antigravity (agy)',
    'syncDir.targetOverrideLabel': 'Target Folder Override (optional)',
    'syncDir.targetOverridePh': 'Blank = preserve source folder',
    'syncDir.includeThought': 'Include <thought>',
    'syncDir.overwrite': 'Overwrite existing targets',
    'syncDir.cancel': 'Cancel',
    'syncDir.submit': 'Sync Folder',
    'syncDir.hintDetected': '{n} {from} session(s) detected in this folder \u2192 will copy to {to}.',
    'syncDir.hintCustom': 'Will copy all top-level {from} sessions in "{cwd}" to {to}.',
    'syncDir.hintChoose': 'Choose a workspace folder to sync.',
    'syncDir.errNoFolder': 'Please enter or select a workspace folder.',
    'syncDir.doneFallback': 'Synced {from} \u2192 {to} for {cwd}',

    'confirm.defaultTitle': 'Confirm action',
    'confirm.cancel': 'Cancel',
    'confirm.confirm': 'Confirm',

    'trim.title': 'Lighten Conversation',
    'trim.keepLabel': 'History turns to keep',
    'trim.preset50': 'Keep newest 50% of turns (recommended)',
    'trim.presetLast20': 'Keep last 20 turns',
    'trim.presetLast50': 'Keep last 50 turns',
    'trim.presetAll': 'Keep all turns (only strip heavy logs & sidecars)',
    'trim.purgeSidecars': 'Purge heavy sidecars (brain images/logs, subagent files, file-history)',
    'trim.truncateTools': 'Truncate large tool outputs (> 2,000 chars)',
    'trim.stripThinking': 'Strip internal thinking / reasoning blocks',
    'trim.cancel': 'Cancel',
    'trim.submit': 'Lighten Now',
    'trim.labelFmt': '{title} \u00b7 ({providers}) \u00b7 {turns} turns \u00b7 {size}',
    'trim.toast': 'Lightened ({count} {copyWord}): {before} \u2192 {after} (freed {freed})',

    'cleanup.title': 'Storage Clean Up',
    'cleanup.totalStorage': 'Total Storage',
    'cleanup.agyLabel': 'Antigravity (agy)',
    'cleanup.claudeLabel': 'Claude Code (claude)',
    'cleanup.orphansSummaryLabel': 'Orphans / Empty / Subagents',
    'cleanup.orphans': 'Clean orphaned sidecar folders & VACUUM SQLite index',
    'cleanup.empty': 'Delete empty 0-turn sessions',
    'cleanup.subagents': 'Delete subagent logs & Claude subagents/ folders',
    'cleanup.olderDays': 'Also delete sessions older than (days)',
    'cleanup.olderPh': 'Optional (e.g. 30)',
    'cleanup.largerMb': 'Or larger than (MB)',
    'cleanup.largerPh': 'Optional (e.g. 10)',
    'cleanup.scope': 'Scope',
    'cleanup.scopeAll': 'All workspaces',
    'cleanup.scopeCwd': 'Current workspace filter only',
    'cleanup.close': 'Close',
    'cleanup.previewBtn': 'Preview (Dry Run)',
    'cleanup.runBtn': 'Clean Up Now',
    'cleanup.confirmTitle': 'Run storage cleanup?',
    'cleanup.confirmMsg': 'This will permanently remove the selected orphans/sessions. Consider clicking "Preview (Dry Run)" first.',
    'cleanup.estimatedFreed': 'Estimated freed: {freed}',
    'cleanup.nothingMatched': 'Nothing matches these cleanup criteria.',
    'cleanup.moreItems': '... and {n} more item(s)',
    'cleanup.done': 'Cleanup complete.',

    'as.title': 'Background Auto-Sync',
    'as.disabled': 'Disabled',
    'as.enabled': 'Enabled',
    'as.runningNow': 'Running now',
    'as.lastRunNever': 'Last run: never',
    'as.lastRunAt': 'Last run: {rel} ({abs})',
    'as.enableCheck': 'Enable automatic background sync',
    'as.direction': 'Sync Direction',
    'as.dirBoth': 'Bidirectional (agy \u2194 claude)',
    'as.dirAgy2Claude': 'Antigravity \u2192 Claude Code only',
    'as.dirClaude2Agy': 'Claude Code \u2192 Antigravity only',
    'as.interval': 'Check Interval',
    'as.everySec': 'Every {s}s',
    'as.scope': 'Workspace Scope',
    'as.scopeAll': 'All workspaces',
    'as.scopeWatched': 'Selected folders only',
    'as.includeThinking': 'Include thinking (<thought>) in synced sessions',
    'as.watchedActive': 'Watched folders (active scope)',
    'as.watchedInactive': 'Watched folders (used when scope is "Selected folders only")',
    'as.watchAll': 'Select all detected',
    'as.watchClear': 'Clear',
    'as.watchAddPh': 'Add custom folder path (e.g. C:\\projects\\app)',
    'as.watchAddBtn': 'Add folder',
    'as.noWorkspaces': 'No workspaces detected yet. Add a folder path below.',
    'as.sessionsCount': '{n} sessions',
    'as.removeFolder': 'Remove',
    'as.unsaved': 'Unsaved changes',
    'as.hint': 'Syncs only when a session gains turns or updates, preventing echo loops.',
    'as.saveBtn': 'Save Settings',
    'as.syncNowBtn': 'Sync Now',
    'as.logTitle': 'Sync Activity Log',
    'as.entriesOne': '{n} entry',
    'as.entriesMany': '{n} entries',
    'as.colTime': 'Time',
    'as.colDir': 'Direction',
    'as.colSession': 'Session & Folder',
    'as.colStatus': 'Status',
    'as.colTarget': 'Target / Resume',
    'as.noActivity': 'No sync activity yet.',
    'as.noOps': 'No sync operations recorded yet.',
    'as.pillSyncing': 'Auto-Sync: syncing...',
    'as.pillOn': 'Auto-Sync: ON ({s}s)',
    'as.pillOff': 'Auto-Sync: OFF',
    'as.lastErr': 'Last auto-sync error: {err}',
    'as.savedToast': 'Auto-Sync settings saved.',
    'as.syncDoneToast': 'Sync completed.',

    'toast.copied': 'Copied',
    'toast.copyFailed': 'Could not copy to clipboard',
    'toast.dismiss': 'Dismiss',
    'toast.copy': 'Copy',
    'toast.cdAndCmd': 'cd + cmd',
    'toast.itemsFailed': '{n} item(s) failed: {err}',

    'time.justNow': 'just now',
    'time.mAgo': '{n}m ago',
    'time.hAgo': '{n}h ago',
    'time.dAgo': '{n}d ago',
    'sync.label': 'Synced \u2194 {names}',

    'help.title': 'Visual UI Guide & FAQ',
    'help.subtitle': 'Click numbered callouts below to see what each control does and when to use it.',
    'help.close': 'Close',
    'help.tab.overview': 'Overview & Filters',
    'help.tab.providers': 'Providers & Sync',
    'help.tab.drawer': 'Preview & Lighten',
    'help.tab.cleanup': 'Clean Up',
    'help.tab.autosync': 'Auto-Sync & Use Cases',
    'help.useWhen': 'Use when:',
    'help.imgMissing': 'Guide screenshot not generated yet ({src}). Run docs screenshot capture to populate.',
    'help.usecasesTitle': 'Real-world Use Cases',
  },
  vi: {
    'page.title': 'mcas \u2014 Qu\u1ea3n l\u00fd phi\u00ean Coding Agent',
    'brand.sub': 'Qu\u1ea3n l\u00fd phi\u00ean Coding Agent',
    'top.connecting': '\u0111ang k\u1ebft n\u1ed1i...',
    'top.ready': 's\u1eb5n s\u00e0ng',
    'top.autosyncTitle': 'M\u1edf c\u00e0i \u0111\u1eb7t & nh\u1eadt k\u00fd Auto-Sync',
    'top.autostart': 'Kh\u1edfi \u0111\u1ed9ng c\u00f9ng Windows',
    'top.autostartTitle': 'T\u1ef1 \u0111\u1ed9ng ch\u1ea1y bi\u1ec3u t\u01b0\u1ee3ng khay mcas khi \u0111\u0103ng nh\u1eadp Windows',
    'top.autostartEnabled': '\u0110\u00e3 b\u1eadt kh\u1edfi \u0111\u1ed9ng c\u00f9ng Windows.',
    'top.autostartDisabled': '\u0110\u00e3 t\u1eaft kh\u1edfi \u0111\u1ed9ng c\u00f9ng Windows.',
    'top.refresh': 'L\u00e0m m\u1edbi',
    'top.refreshTitle': 'L\u00e0m m\u1edbi tr\u1ea1ng th\u00e1i v\u00e0 danh s\u00e1ch phi\u00ean',
    'top.stopServer': 'D\u1eebng m\u00e1y ch\u1ee7',
    'top.stopServerTitle': 'D\u1eebng m\u00e1y ch\u1ee7 mcas v\u00e0 \u1ea9n bi\u1ec3u t\u01b0\u1ee3ng khay',
    'top.help': 'H\u01b0\u1edbng d\u1eabn',
    'top.helpTitle': 'M\u1edf h\u01b0\u1edbng d\u1eabn tr\u1ef1c quan & FAQ (?)',
    'top.langTitle': 'Chuy\u1ec3n ng\u00f4n ng\u1eef',
    'top.themeToLight': 'Chuy\u1ec3n sang giao di\u1ec7n s\u00e1ng',
    'top.themeToDark': 'Chuy\u1ec3n sang giao di\u1ec7n t\u1ed1i',
    'top.themeAria': 'Chuy\u1ec3n giao di\u1ec7n s\u00e1ng/t\u1ed1i',

    'conn.lost': 'M\u1ea5t k\u1ebft n\u1ed1i.',
    'conn.unreachable': 'Kh\u00f4ng k\u1ebft n\u1ed1i \u0111\u01b0\u1ee3c m\u00e1y ch\u1ee7 UI. H\u00e3y ch\u1ea1y l\u1ea1i "mcas ui".',
    'conn.reconnect': 'K\u1ebft n\u1ed1i l\u1ea1i',
    'conn.loadFailed': 'T\u1ea3i danh s\u00e1ch phi\u00ean th\u1ea5t b\u1ea1i',
    'conn.loadFailedToast': 'Kh\u00f4ng t\u1ea3i \u0111\u01b0\u1ee3c danh s\u00e1ch phi\u00ean.',

    'quit.title': 'D\u1eebng m\u00e1y ch\u1ee7 mcas',
    'quit.msg': 'D\u1eebng web UI mcas, Auto-Sync ch\u1ea1y n\u1ec1n v\u00e0 bi\u1ec3u t\u01b0\u1ee3ng khay? Ch\u1ea1y l\u1ea1i "mcas ui" \u0111\u1ec3 m\u1edf l\u1ea1i.',
    'quit.stoppedTitle': 'M\u00e1y ch\u1ee7 mcas \u0111\u00e3 d\u1eebng',
    'quit.stoppedDesc': 'Web server v\u00e0 bi\u1ec3u t\u01b0\u1ee3ng khay \u0111\u00e3 t\u1eaft. B\u1ea1n c\u00f3 th\u1ec3 \u0111\u00f3ng tab n\u00e0y ho\u1eb7c ch\u1ea1y "mcas ui" \u0111\u1ec3 kh\u1edfi \u0111\u1ed9ng l\u1ea1i.',

    'tabs.aria': 'Ch\u1ebf \u0111\u1ed9 xem ch\u00ednh',
    'tabs.sessions': 'Phi\u00ean & Sao ch\u00e9p/\u0110\u1ed3ng b\u1ed9',
    'tabs.autosync': 'Auto-Sync & Ho\u1ea1t \u0111\u1ed9ng',
    'tabs.badgeSync': '\u0110\u1ed2NG B\u1ed8',
    'tabs.badgeOn': 'B\u1eacT',
    'tabs.badgeOff': 'T\u1eaeT',

    'filter.providerAria': 'L\u1ecdc theo provider',
    'filter.all': 'T\u1ea5t c\u1ea3',
    'filter.soon': 'S\u1eafp c\u00f3',
    'filter.soonTitle': '{name} (S\u1eafp h\u1ed7 tr\u1ee3)',
    'filter.providerTitle': 'Hi\u1ec3n th\u1ecb phi\u00ean {name}',
    'filter.chipTitle': 'L\u1ecdc theo phi\u00ean {name}',
    'filter.chipSoonTitle': 'H\u1ed7 tr\u1ee3 {name} s\u1eafp ra m\u1eaft',
    'filter.folderAria': 'L\u1ecdc theo th\u01b0 m\u1ee5c workspace',
    'filter.allFolders': 'T\u1ea5t c\u1ea3 th\u01b0 m\u1ee5c',
    'filter.allFoldersCount': 'T\u1ea5t c\u1ea3 th\u01b0 m\u1ee5c ({n})',
    'filter.customFolderOpt': 'Nh\u1eadp \u0111\u01b0\u1eddng d\u1eabn th\u01b0 m\u1ee5c...',
    'filter.customFolderPh': 'Nh\u1eadp \u0111\u01b0\u1eddng d\u1eabn (VD: C:\\repo)',
    'filter.apply': '\u00c1p d\u1ee5ng',
    'filter.clear': 'X\u00f3a',
    'filter.clearCustomTitle': 'X\u00f3a th\u01b0 m\u1ee5c t\u00f9y ch\u1ecdn',
    'filter.searchPh': 'T\u00ecm ti\u00eau \u0111\u1ec1, n\u1ed9i dung, ID, th\u01b0 m\u1ee5c...',
    'filter.searchClearTitle': 'X\u00f3a t\u00ecm ki\u1ebfm',
    'filter.includeAll': 'G\u1ed3m subagent / IDE / tr\u1ed1ng',
    'filter.sortAria': 'S\u1eafp x\u1ebfp h\u1ed9i tho\u1ea1i',
    'filter.sortNewest': 'S\u1eafp x\u1ebfp: M\u1edbi nh\u1ea5t',
    'filter.sortLargest': 'S\u1eafp x\u1ebfp: N\u1eb7ng nh\u1ea5t',
    'filter.sortTurns': 'S\u1eafp x\u1ebfp: Nhi\u1ec1u l\u01b0\u1ee3t nh\u1ea5t',
    'filter.sortOldest': 'S\u1eafp x\u1ebfp: C\u0169 nh\u1ea5t',
    'filter.cleanUp': 'D\u1ecdn d\u1eb9p...',
    'filter.syncFolder': '\u0110\u1ed3ng b\u1ed9 th\u01b0 m\u1ee5c \u2192 ...',
    'filter.syncThisFolder': '\u0110\u1ed3ng b\u1ed9 th\u01b0 m\u1ee5c n\u00e0y \u2192 ...',
    'filter.clearChipTitle': 'X\u00f3a b\u1ed9 l\u1ecdc',
    'filter.chipProvider': 'Provider: {val}',
    'filter.chipFolder': 'Th\u01b0 m\u1ee5c: {val}',
    'filter.chipSearch': 'T\u00ecm: "{val}"',

    'sel.zero': '\u0110\u00e3 ch\u1ecdn 0',
    'sel.countBytes': '\u0110\u00e3 ch\u1ecdn {n} \u00b7 {size}',
    'sel.clear': 'B\u1ecf ch\u1ecdn',
    'sel.to': 'Sang',
    'sel.targetAria': 'Provider \u0111\u00edch cho c\u00e1c phi\u00ean \u0111\u00e3 ch\u1ecdn',
    'sel.targetCwdPh': 'Th\u01b0 m\u1ee5c \u0111\u00edch (tr\u1ed1ng = gi\u1eef th\u01b0 m\u1ee5c g\u1ed1c)',
    'sel.includeThought': 'K\u00e8m <thought>',
    'sel.newId': 'T\u1ea1o UUID m\u1edbi',
    'sel.overwrite': 'Ghi \u0111\u00e8',
    'sel.copySelected': 'Sao ch\u00e9p \u0111\u00e3 ch\u1ecdn',
    'sel.deleteSelected': 'X\u00f3a \u0111\u00e3 ch\u1ecdn',
    'sel.selectAll': 'Ch\u1ecdn t\u1ea5t c\u1ea3 \u0111ang hi\u1ec7n',

    'list.loading': '\u0110ang t\u1ea3i phi\u00ean...',
    'list.loadingConvs': '\u0110ang t\u1ea3i h\u1ed9i tho\u1ea1i...',
    'list.countOne': '{n} cu\u1ed9c h\u1ed9i tho\u1ea1i',
    'list.countMany': '{n} cu\u1ed9c h\u1ed9i tho\u1ea1i',
    'list.countGroupedOne': '{n} cu\u1ed9c h\u1ed9i tho\u1ea1i ({raw} b\u1ea3n ghi)',
    'list.countGroupedMany': '{n} cu\u1ed9c h\u1ed9i tho\u1ea1i ({raw} b\u1ea3n ghi)',
    'list.emptyFiltered': 'Kh\u00f4ng c\u00f3 phi\u00ean n\u00e0o kh\u1edbp b\u1ed9 l\u1ecdc hi\u1ec7n t\u1ea1i.',
    'list.emptyNone': 'Ch\u01b0a t\u00ecm th\u1ea5y phi\u00ean n\u00e0o.',
    'list.showAllProviders': 'Hi\u1ec7n t\u1ea5t c\u1ea3 provider trong th\u01b0 m\u1ee5c n\u00e0y',
    'list.resetFilters': '\u0110\u1eb7t l\u1ea1i b\u1ed9 l\u1ecdc',
    'list.footShowing': '\u0110ang hi\u1ec7n {shown} b\u1ea3n ghi ({grouped} nh\u00f3m) tr\u00ean t\u1ed5ng {total} b\u1ea3n ghi kh\u1edbp',
    'list.loadMore': 'T\u1ea3i th\u00eam',

    'row.untitled': '(ch\u01b0a c\u00f3 ti\u00eau \u0111\u1ec1)',
    'row.selectAria': 'Ch\u1ecdn {title}',
    'row.idTipSingle': 'Nh\u1ea5p \u0111\u1ec3 sao ch\u00e9p \u0111\u1ea7y \u0111\u1ee7 ID: {id}',
    'row.filterFolderTip': 'L\u1ecdc theo th\u01b0 m\u1ee5c: {cwd}',
    'row.noWorkspace': '(kh\u00f4ng c\u00f3 workspace)',
    'row.turns': '{n} l\u01b0\u1ee3t',
    'row.providersBtn': 'Providers ({list}) \u25be',
    'row.lighten': 'L\u00e0m nh\u1eb9',
    'row.lightenTitle': 'C\u1eaft g\u1ecdn tr\u1ef1c ti\u1ebfp \u0111\u1ec3 gi\u1ea3m dung l\u01b0\u1ee3ng',
    'row.preview': 'Xem tr\u01b0\u1edbc',

    'dd.tagTitle': 'Qu\u1ea3n l\u00fd c\u00e1c provider \u0111ang l\u01b0u phi\u00ean n\u00e0y',
    'dd.title': 'Provider \u0111ang gi\u1eef phi\u00ean n\u00e0y',
    'dd.synced': '\u0111\u00e3 \u0111\u1ed3ng b\u1ed9',
    'dd.present': '\u0111ang c\u00f3',
    'dd.notHere': 'ch\u01b0a c\u00f3',
    'dd.add': '+ Th\u00eam',
    'dd.remove': '- G\u1ee1 b\u1ecf',
    'dd.includeThought': 'K\u00e8m kh\u1ed1i <thought> khi \u0111\u1ed3ng b\u1ed9',
    'dd.warnDeleteAll': '\u0110\u00e3 b\u1ecf ch\u1ecdn t\u1ea5t c\u1ea3 provider \u2014 b\u1ea5m \u00c1p d\u1ee5ng s\u1ebd h\u1ecfi x\u00e1c nh\u1eadn x\u00f3a v\u0129nh vi\u1ec5n phi\u00ean n\u00e0y.',
    'dd.untickAll': 'B\u1ecf ch\u1ecdn h\u1ebft',
    'dd.apply': '\u00c1p d\u1ee5ng',
    'dd.applyDelete': '\u00c1p d\u1ee5ng (X\u00f3a)',

    'copy.copiedTo': '\u0110\u00e3 sao ch\u00e9p sang {to}',
    'copy.copiedBatch': '\u0110\u00e3 sao ch\u00e9p {n} phi\u00ean sang {to}',
    'copy.addedTo': '\u0110\u00e3 th\u00eam v\u00e0o {to}',
    'copy.removedFrom': '\u0110\u00e3 g\u1ee1 kh\u1ecfi {list} (gi\u1ea3i ph\u00f3ng {freed})',
    'copy.providersUpdated': '\u0110\u00e3 c\u1eadp nh\u1eadt provider.',

    'delete.confirmTitle': 'X\u00f3a ho\u00e0n to\u00e0n cu\u1ed9c h\u1ed9i tho\u1ea1i?',
    'delete.confirmMsg': 'X\u00f3a v\u0129nh vi\u1ec5n "{title}" kh\u1ecfi {providers} ({count} {copyWord}, {size})? H\u00e0nh \u0111\u1ed9ng n\u00e0y kh\u00f4ng th\u1ec3 ho\u00e0n t\u00e1c.',
    'delete.copyOne': 'b\u1ea3n sao',
    'delete.copyMany': 'b\u1ea3n sao',
    'delete.confirmBtn': 'X\u00f3a ho\u00e0n to\u00e0n',
    'delete.done': '\u0110\u00e3 x\u00f3a cu\u1ed9c h\u1ed9i tho\u1ea1i.',
    'delete.bulkTitle': 'X\u00f3a {n} cu\u1ed9c h\u1ed9i tho\u1ea1i \u0111\u00e3 ch\u1ecdn?',
    'delete.bulkMsg': 'X\u00f3a v\u0129nh vi\u1ec5n {n} cu\u1ed9c h\u1ed9i tho\u1ea1i ({copies} b\u1ea3n tr\u00ean provider, ~{size})? Kh\u00f4ng th\u1ec3 ho\u00e0n t\u00e1c.',
    'delete.bulkDone': '\u0110\u00e3 x\u00f3a c\u00e1c phi\u00ean \u0111\u00e3 ch\u1ecdn.',

    'drawer.defaultTitle': 'Xem tr\u01b0\u1edbc phi\u00ean',
    'drawer.close': '\u0110\u00f3ng',
    'drawer.closeAria': '\u0110\u00f3ng xem tr\u01b0\u1edbc',
    'drawer.resumeDir': 'Th\u01b0 m\u1ee5c Resume',
    'drawer.resumeCmd': 'L\u1ec7nh Resume',
    'drawer.copy': 'Sao ch\u00e9p',
    'drawer.copyCmd': 'Ch\u00e9p l\u1ec7nh',
    'drawer.copyToClaude': 'Ch\u00e9p \u2192 Claude',
    'drawer.copyToAgy': 'Ch\u00e9p \u2192 AGY',
    'drawer.includeThought': 'K\u00e8m <thought>',
    'drawer.trim': 'L\u00e0m nh\u1eb9 / C\u1eaft g\u1ecdn',
    'drawer.delete': 'X\u00f3a ho\u00e0n to\u00e0n',
    'drawer.loading': '\u0110ang t\u1ea3i phi\u00ean...',
    'drawer.loadingTurns': '\u0110ang t\u1ea3i c\u00e1c l\u01b0\u1ee3t h\u1ed9i tho\u1ea1i...',
    'drawer.loadFailed': 'Kh\u00f4ng t\u1ea3i \u0111\u01b0\u1ee3c phi\u00ean',
    'drawer.emptyTurns': 'Phi\u00ean n\u00e0y kh\u00f4ng c\u00f3 l\u01b0\u1ee3t h\u1ed9i tho\u1ea1i hi\u1ec3n th\u1ecb.',
    'drawer.idLbl': 'ID: ',
    'drawer.folderLbl': 'Th\u01b0 m\u1ee5c: ',
    'drawer.none': '(tr\u1ed1ng)',
    'drawer.updatedLbl': 'C\u1eadp nh\u1eadt: ',
    'drawer.turnsLbl': 'S\u1ed1 l\u01b0\u1ee3t: ',
    'drawer.sizeLbl': 'Dung l\u01b0\u1ee3ng: ',
    'drawer.sizeDetail': '{total} (ch\u00ednh: {main}, file ph\u1ee5: {sidecar})',
    'drawer.providersLbl': 'Providers: ',
    'drawer.thinkingChars': 'Thinking ({n} k\u00fd t\u1ef1)',
    'drawer.toolCall': 'G\u1ecdi tool: {name}{id}',
    'drawer.toolResult': 'K\u1ebft qu\u1ea3 tool{err}: {name}{id}',
    'drawer.emptyOutput': '(k\u1ebft qu\u1ea3 r\u1ed7ng)',

    'syncDir.title': '\u0110\u1ed3ng b\u1ed9 phi\u00ean theo th\u01b0 m\u1ee5c',
    'syncDir.sourceLabel': 'Th\u01b0 m\u1ee5c workspace ngu\u1ed3n',
    'syncDir.customPh': 'Nh\u1eadp \u0111\u01b0\u1eddng d\u1eabn (VD: C:\\_personal\\_empty)',
    'syncDir.directionLabel': 'Chi\u1ec1u \u0111\u1ed3ng b\u1ed9',
    'syncDir.dirAgyToClaude': 'Antigravity (agy) \u2192 Claude Code (claude)',
    'syncDir.dirClaudeToAgy': 'Claude Code (claude) \u2192 Antigravity (agy)',
    'syncDir.targetOverrideLabel': 'Ghi \u0111\u00e8 th\u01b0 m\u1ee5c \u0111\u00edch (t\u00f9y ch\u1ecdn)',
    'syncDir.targetOverridePh': '\u0110\u1ec3 tr\u1ed1ng = gi\u1eef nguy\u00ean th\u01b0 m\u1ee5c ngu\u1ed3n',
    'syncDir.includeThought': 'K\u00e8m <thought>',
    'syncDir.overwrite': 'Ghi \u0111\u00e8 phi\u00ean \u0111\u00edch \u0111\u00e3 c\u00f3',
    'syncDir.cancel': 'H\u1ee7y',
    'syncDir.submit': '\u0110\u1ed3ng b\u1ed9 th\u01b0 m\u1ee5c',
    'syncDir.hintDetected': 'Ph\u00e1t hi\u1ec7n {n} phi\u00ean {from} trong th\u01b0 m\u1ee5c n\u00e0y \u2192 s\u1ebd ch\u00e9p sang {to}.',
    'syncDir.hintCustom': 'S\u1ebd ch\u00e9p t\u1ea5t c\u1ea3 phi\u00ean {from} trong "{cwd}" sang {to}.',
    'syncDir.hintChoose': 'H\u00e3y ch\u1ecdn th\u01b0 m\u1ee5c workspace c\u1ea7n \u0111\u1ed3ng b\u1ed9.',
    'syncDir.errNoFolder': 'Vui l\u00f2ng nh\u1eadp ho\u1eb7c ch\u1ecdn th\u01b0 m\u1ee5c workspace.',
    'syncDir.doneFallback': '\u0110\u00e3 \u0111\u1ed3ng b\u1ed9 {from} \u2192 {to} cho {cwd}',

    'confirm.defaultTitle': 'X\u00e1c nh\u1eadn thao t\u00e1c',
    'confirm.cancel': 'H\u1ee7y',
    'confirm.confirm': 'X\u00e1c nh\u1eadn',

    'trim.title': 'L\u00e0m nh\u1eb9 cu\u1ed9c h\u1ed9i tho\u1ea1i',
    'trim.keepLabel': 'S\u1ed1 l\u01b0\u1ee3t h\u1ed9i tho\u1ea1i gi\u1eef l\u1ea1i',
    'trim.preset50': 'Gi\u1eef 50% l\u01b0\u1ee3t m\u1edbi nh\u1ea5t (khuy\u00ean d\u00f9ng)',
    'trim.presetLast20': 'Gi\u1eef 20 l\u01b0\u1ee3t cu\u1ed1i',
    'trim.presetLast50': 'Gi\u1eef 50 l\u01b0\u1ee3t cu\u1ed1i',
    'trim.presetAll': 'Gi\u1eef t\u1ea5t c\u1ea3 l\u01b0\u1ee3t (ch\u1ec9 d\u1ecdn log n\u1eb7ng & file ph\u1ee5)',
    'trim.purgeSidecars': 'X\u00f3a file ph\u1ee5 n\u1eb7ng (\u1ea3nh/log brain, subagent, file-history)',
    'trim.truncateTools': 'C\u1eaft ng\u1eafn k\u1ebft qu\u1ea3 tool qu\u00e1 d\u00e0i (> 2.000 k\u00fd t\u1ef1)',
    'trim.stripThinking': 'X\u00f3a kh\u1ed1i suy ngh\u0129 n\u1ed9i b\u1ed9 (<thought>)',
    'trim.cancel': 'H\u1ee7y',
    'trim.submit': 'L\u00e0m nh\u1eb9 ngay',
    'trim.labelFmt': '{title} \u00b7 ({providers}) \u00b7 {turns} l\u01b0\u1ee3t \u00b7 {size}',
    'trim.toast': '\u0110\u00e3 l\u00e0m nh\u1eb9 ({count} {copyWord}): {before} \u2192 {after} (gi\u1ea3i ph\u00f3ng {freed})',

    'cleanup.title': 'D\u1ecdn d\u1eb9p dung l\u01b0\u1ee3ng',
    'cleanup.totalStorage': 'T\u1ed5ng dung l\u01b0\u1ee3ng',
    'cleanup.agyLabel': 'Antigravity (agy)',
    'cleanup.claudeLabel': 'Claude Code (claude)',
    'cleanup.orphansSummaryLabel': 'M\u1ed3 c\u00f4i / Tr\u1ed1ng / Subagent',
    'cleanup.orphans': 'D\u1ecdn th\u01b0 m\u1ee5c ph\u1ee5 m\u1ed3 c\u00f4i & n\u00e9n SQLite index (VACUUM)',
    'cleanup.empty': 'X\u00f3a c\u00e1c phi\u00ean tr\u1ed1ng 0 l\u01b0\u1ee3t',
    'cleanup.subagents': 'X\u00f3a log subagent & th\u01b0 m\u1ee5c subagents/ c\u1ee7a Claude',
    'cleanup.olderDays': 'X\u00f3a th\u00eam phi\u00ean c\u0169 h\u01a1n (ng\u00e0y)',
    'cleanup.olderPh': 'T\u00f9y ch\u1ecdn (VD: 30)',
    'cleanup.largerMb': 'Ho\u1eb7c l\u1edbn h\u01a1n (MB)',
    'cleanup.largerPh': 'T\u00f9y ch\u1ecdn (VD: 10)',
    'cleanup.scope': 'Ph\u1ea1m vi',
    'cleanup.scopeAll': 'T\u1ea5t c\u1ea3 workspace',
    'cleanup.scopeCwd': 'Ch\u1ec9 th\u01b0 m\u1ee5c \u0111ang l\u1ecdc',
    'cleanup.close': '\u0110\u00f3ng',
    'cleanup.previewBtn': 'Xem tr\u01b0\u1edbc (Ch\u1ea1y th\u1eed)',
    'cleanup.runBtn': 'D\u1ecdn d\u1eb9p ngay',
    'cleanup.confirmTitle': 'Ch\u1ea1y d\u1ecdn d\u1eb9p dung l\u01b0\u1ee3ng?',
    'cleanup.confirmMsg': 'Thao t\u00e1c n\u00e0y s\u1ebd x\u00f3a v\u0129nh vi\u1ec5n c\u00e1c m\u1ee5c m\u1ed3 c\u00f4i/phi\u00ean \u0111\u00e3 ch\u1ecdn. N\u00ean b\u1ea5m "Xem tr\u01b0\u1edbc (Ch\u1ea1y th\u1eed)" tr\u01b0\u1edbc.',
    'cleanup.estimatedFreed': '\u01af\u1edbc t\u00ednh gi\u1ea3i ph\u00f3ng: {freed}',
    'cleanup.nothingMatched': 'Kh\u00f4ng c\u00f3 m\u1ee5c n\u00e0o kh\u1edbp ti\u00eau ch\u00ed d\u1ecdn d\u1eb9p.',
    'cleanup.moreItems': '... v\u00e0 {n} m\u1ee5c kh\u00e1c',
    'cleanup.done': '\u0110\u00e3 d\u1ecdn d\u1eb9p xong.',

    'as.title': 'Auto-Sync ch\u1ea1y n\u1ec1n',
    'as.disabled': '\u0110ang t\u1eaft',
    'as.enabled': '\u0110ang b\u1eadt',
    'as.runningNow': '\u0110ang \u0111\u1ed3ng b\u1ed9',
    'as.lastRunNever': 'L\u1ea7n ch\u1ea1y cu\u1ed1i: ch\u01b0a c\u00f3',
    'as.lastRunAt': 'L\u1ea7n ch\u1ea1y cu\u1ed1i: {rel} ({abs})',
    'as.enableCheck': 'B\u1eadt t\u1ef1 \u0111\u1ed9ng \u0111\u1ed3ng b\u1ed9 n\u1ec1n',
    'as.direction': 'Chi\u1ec1u \u0111\u1ed3ng b\u1ed9',
    'as.dirBoth': 'Hai chi\u1ec1u (agy \u2194 claude)',
    'as.dirAgy2Claude': 'Ch\u1ec9 Antigravity \u2192 Claude Code',
    'as.dirClaude2Agy': 'Ch\u1ec9 Claude Code \u2192 Antigravity',
    'as.interval': 'Chu k\u1ef3 ki\u1ec3m tra',
    'as.everySec': 'M\u1ed7i {s} gi\u00e2y',
    'as.scope': 'Ph\u1ea1m vi th\u01b0 m\u1ee5c',
    'as.scopeAll': 'T\u1ea5t c\u1ea3 workspace',
    'as.scopeWatched': 'Ch\u1ec9 c\u00e1c th\u01b0 m\u1ee5c \u0111\u00e3 ch\u1ecdn',
    'as.includeThinking': 'K\u00e8m <thought> trong c\u00e1c phi\u00ean \u0111\u1ed3ng b\u1ed9',
    'as.watchedActive': 'Th\u01b0 m\u1ee5c theo d\u00f5i (\u0111ang \u00e1p d\u1ee5ng)',
    'as.watchedInactive': 'Th\u01b0 m\u1ee5c theo d\u00f5i (d\u00f9ng khi ch\u1ecdn "Ch\u1ec9 c\u00e1c th\u01b0 m\u1ee5c \u0111\u00e3 ch\u1ecdn")',
    'as.watchAll': 'Ch\u1ecdn t\u1ea5t c\u1ea3 \u0111\u00e3 ph\u00e1t hi\u1ec7n',
    'as.watchClear': 'B\u1ecf ch\u1ecdn',
    'as.watchAddPh': 'Th\u00eam \u0111\u01b0\u1eddng d\u1eabn th\u01b0 m\u1ee5c (VD: C:\\projects\\app)',
    'as.watchAddBtn': 'Th\u00eam th\u01b0 m\u1ee5c',
    'as.noWorkspaces': 'Ch\u01b0a ph\u00e1t hi\u1ec7n workspace n\u00e0o. H\u00e3y th\u00eam \u0111\u01b0\u1eddng d\u1eabn b\u00ean d\u01b0\u1edbi.',
    'as.sessionsCount': '{n} phi\u00ean',
    'as.removeFolder': 'G\u1ee1',
    'as.unsaved': 'Ch\u01b0a l\u01b0u thay \u0111\u1ed5i',
    'as.hint': 'Ch\u1ec9 \u0111\u1ed3ng b\u1ed9 khi phi\u00ean c\u00f3 l\u01b0\u1ee3t m\u1edbi ho\u1eb7c c\u1eadp nh\u1eadt, tr\u00e1nh l\u1eb7p ng\u01b0\u1ee3c.',
    'as.saveBtn': 'L\u01b0u c\u00e0i \u0111\u1eb7t',
    'as.syncNowBtn': '\u0110\u1ed3ng b\u1ed9 ngay',
    'as.logTitle': 'Nh\u1eadt k\u00fd ho\u1ea1t \u0111\u1ed9ng \u0111\u1ed3ng b\u1ed9',
    'as.entriesOne': '{n} d\u00f2ng',
    'as.entriesMany': '{n} d\u00f2ng',
    'as.colTime': 'Th\u1eddi gian',
    'as.colDir': 'Chi\u1ec1u',
    'as.colSession': 'Phi\u00ean & Th\u01b0 m\u1ee5c',
    'as.colStatus': 'Tr\u1ea1ng th\u00e1i',
    'as.colTarget': '\u0110\u00edch / Resume',
    'as.noActivity': 'Ch\u01b0a c\u00f3 ho\u1ea1t \u0111\u1ed9ng \u0111\u1ed3ng b\u1ed9.',
    'as.noOps': 'Ch\u01b0a ghi nh\u1eadn thao t\u00e1c \u0111\u1ed3ng b\u1ed9 n\u00e0o.',
    'as.pillSyncing': 'Auto-Sync: \u0111ang \u0111\u1ed3ng b\u1ed9...',
    'as.pillOn': 'Auto-Sync: B\u1eacT ({s}s)',
    'as.pillOff': 'Auto-Sync: T\u1eaeT',
    'as.lastErr': 'L\u1ed7i Auto-Sync g\u1ea7n nh\u1ea5t: {err}',
    'as.savedToast': '\u0110\u00e3 l\u01b0u c\u00e0i \u0111\u1eb7t Auto-Sync.',
    'as.syncDoneToast': '\u0110\u00e3 \u0111\u1ed3ng b\u1ed9 xong.',

    'toast.copied': '\u0110\u00e3 ch\u00e9p',
    'toast.copyFailed': 'Kh\u00f4ng th\u1ec3 sao ch\u00e9p v\u00e0o clipboard',
    'toast.dismiss': '\u0110\u00f3ng',
    'toast.copy': 'Sao ch\u00e9p',
    'toast.cdAndCmd': 'cd + l\u1ec7nh',
    'toast.itemsFailed': '{n} m\u1ee5c th\u1ea5t b\u1ea1i: {err}',

    'time.justNow': 'v\u1eeba xong',
    'time.mAgo': '{n} ph\u00fat tr\u01b0\u1edbc',
    'time.hAgo': '{n} gi\u1edd tr\u01b0\u1edbc',
    'time.dAgo': '{n} ng\u00e0y tr\u01b0\u1edbc',
    'sync.label': '\u0110\u1ed3ng b\u1ed9 \u2194 {names}',

    'help.title': 'H\u01b0\u1edbng d\u1eabn tr\u1ef1c quan & FAQ',
    'help.subtitle': 'Xem c\u00e1c s\u1ed1 \u0111\u00e1nh d\u1ea5u tr\u00ean \u1ea3nh \u0111\u1ec3 bi\u1ebft ch\u1ee9c n\u0103ng t\u1eebng n\u00fat v\u00e0 khi n\u00e0o n\u00ean d\u00f9ng.',
    'help.close': '\u0110\u00f3ng',
    'help.tab.overview': 'T\u1ed5ng quan & B\u1ed9 l\u1ecdc',
    'help.tab.providers': 'Providers & \u0110\u1ed3ng b\u1ed9',
    'help.tab.drawer': 'Xem tr\u01b0\u1edbc & L\u00e0m nh\u1eb9',
    'help.tab.cleanup': 'D\u1ecdn d\u1eb9p',
    'help.tab.autosync': 'Auto-Sync & T\u00ecnh hu\u1ed1ng',
    'help.useWhen': 'D\u00f9ng khi:',
    'help.imgMissing': 'Ch\u01b0a t\u1ea1o \u1ea3nh minh h\u1ecda ({src}). H\u00e3y ch\u1ea1y script ch\u1ee5p \u1ea3nh h\u01b0\u1edbng d\u1eabn.',
    'help.usecasesTitle': 'C\u00e1c t\u00ecnh hu\u1ed1ng s\u1eed d\u1ee5ng th\u1ef1c t\u1ebf',
  },
};

export function getLang() {
  return currentLang;
}

export function t(key, vars) {
  const dict = DICT[currentLang] || DICT.en;
  let str = dict[key];
  if (str === undefined) str = DICT.en[key];
  if (str === undefined) return key;
  if (vars && typeof vars === 'object') {
    for (const [k, v] of Object.entries(vars)) {
      str = str.replaceAll(`{${k}}`, String(v ?? ''));
    }
  }
  return str;
}

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    node.setAttribute(k, String(v));
  }
  return node;
}

/**
 * Build a crisp inline SVG flag icon (no emoji fallback issues on Windows).
 * @param {'en'|'vi'} lang
 * @returns {SVGElement}
 */
export function flagSvg(lang) {
  const code = normalizeLang(lang);
  const svg = svgEl('svg', {
    viewBox: '0 0 20 14',
    width: '18',
    height: '13',
    class: 'flag-svg',
    'aria-hidden': 'true',
  });
  const clipId = `mcas-flag-clip-${code}`;
  const defs = svgEl('defs');
  const clip = svgEl('clipPath', { id: clipId });
  clip.append(svgEl('rect', { x: 0, y: 0, width: 20, height: 14, rx: 2, ry: 2 }));
  defs.append(clip);
  svg.append(defs);

  const g = svgEl('g', { 'clip-path': `url(#${clipId})` });
  if (code === 'vi') {
    g.append(svgEl('rect', { x: 0, y: 0, width: 20, height: 14, fill: '#da251d' }));
    // 5-point yellow star centered at (10, 7)
    g.append(svgEl('polygon', {
      points: '10,2.6 11.2,5.6 14.4,5.6 11.8,7.5 12.8,10.6 10,8.7 7.2,10.6 8.2,7.5 5.6,5.6 8.8,5.6',
      fill: '#ffcd00',
    }));
  } else {
    // US flag: white background + 7 red stripes + blue canton + crisp white star dots
    g.append(svgEl('rect', { x: 0, y: 0, width: 20, height: 14, fill: '#ffffff' }));
    for (let i = 0; i < 7; i++) {
      g.append(svgEl('rect', {
        x: 0,
        y: (i * 2 * 14 / 13).toFixed(2),
        width: 20,
        height: (14 / 13).toFixed(2),
        fill: '#b22234',
      }));
    }
    g.append(svgEl('rect', { x: 0, y: 0, width: 8.8, height: (7 * 14 / 13).toFixed(2), fill: '#3c3b6e' }));
    for (let r = 0; r < 3; r++) {
      for (let c = 0; c < 4; c++) {
        g.append(svgEl('circle', {
          cx: (1.4 + c * 2.0).toFixed(1),
          cy: (1.6 + r * 2.1).toFixed(1),
          r: '0.55',
          fill: '#ffffff',
        }));
      }
    }
  }
  svg.append(g);
  svg.append(svgEl('rect', {
    x: 0.25,
    y: 0.25,
    width: 19.5,
    height: 13.5,
    rx: 2,
    ry: 2,
    fill: 'none',
    stroke: 'rgba(0,0,0,0.18)',
    'stroke-width': '0.5',
  }));
  return svg;
}

export function applyStaticTranslations() {
  document.documentElement.lang = currentLang;
  document.title = t('page.title');

  document.querySelectorAll('[data-i18n]').forEach((el) => {
    const key = el.getAttribute('data-i18n');
    if (key) el.textContent = t(key);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
    const key = el.getAttribute('data-i18n-placeholder');
    if (key) el.setAttribute('placeholder', t(key));
  });
  document.querySelectorAll('[data-i18n-title]').forEach((el) => {
    const key = el.getAttribute('data-i18n-title');
    if (key) el.setAttribute('title', t(key));
  });
  document.querySelectorAll('[data-i18n-aria]').forEach((el) => {
    const key = el.getAttribute('data-i18n-aria');
    if (key) el.setAttribute('aria-label', t(key));
  });

  const flagHost = document.getElementById('langBtnFlag');
  if (flagHost) flagHost.replaceChildren(flagSvg(currentLang));
  const codeEl = document.getElementById('langBtnCode');
  if (codeEl) codeEl.textContent = currentLang.toUpperCase();

  document.querySelectorAll('#langMenu .lang-opt').forEach((opt) => {
    const optLang = opt.getAttribute('data-lang');
    const active = optLang === currentLang;
    opt.classList.toggle('active', active);
    opt.setAttribute('aria-selected', String(active));
    const optFlagHost = opt.querySelector('.lang-opt-flag');
    if (optFlagHost && !optFlagHost.firstChild) {
      optFlagHost.replaceChildren(flagSvg(optLang));
    }
  });
}

export function onLangChange(fn) {
  if (typeof fn === 'function') listeners.add(fn);
  return () => listeners.delete(fn);
}

export function setLang(lang) {
  const next = normalizeLang(lang);
  currentLang = next;
  storageSet('mcas.lang', next);
  applyStaticTranslations();
  for (const fn of listeners) {
    try { fn(next); } catch {}
  }
  return next;
}

export function initI18n() {
  applyStaticTranslations();
}
