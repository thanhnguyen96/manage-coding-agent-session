import { $, el } from '../utils/dom.js';
import { formatResumeCmd } from '../utils/format.js';

export async function copyText(text, btn) {
  let ok = false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      ok = true;
    }
  } catch {}
  if (!ok) {
    try {
      const ta = el('textarea', { value: text, style: { position: 'fixed', opacity: '0' } });
      document.body.append(ta);
      ta.select();
      ok = document.execCommand('copy');
      ta.remove();
    } catch {}
  }
  if (ok && btn) {
    const prev = btn.textContent;
    btn.textContent = 'Copied';
    setTimeout(() => { if (btn.isConnected) btn.textContent = prev; }, 1400);
  }
  if (!ok) toast('Could not copy to clipboard', 'err');
  return ok;
}

export function toast(message, kind = 'info', commands = []) {
  $('liveStatus').textContent = message;
  const host = $('toastHost');
  const box = el('div', { className: 'toast ' + (kind === 'err' ? 'err' : kind === 'ok' ? 'ok' : '') });
  const closeBtn = el('button', {
    type: 'button',
    className: 'sm ghost',
    textContent: '\u00d7',
    title: 'Dismiss',
    style: { padding: '0 6px', border: '0' },
    onclick: () => box.remove(),
  });
  box.append(el('div', { className: 'toast-top' }, el('span', { textContent: message }), closeBtn));

  for (const item of commands) {
    if (!item || !item.cmd) continue;
    const copyBtn = el('button', {
      type: 'button',
      className: 'sm',
      textContent: 'Copy',
      onclick: () => copyText(item.cmd, copyBtn),
    });
    const row = el('div', { className: 'toast-cmd' },
      el('code', { textContent: item.cmd, title: item.dir ? `Dir: ${item.dir} | ${item.cmd}` : item.cmd }),
      copyBtn
    );
    if (item.dir) {
      const copyFullBtn = el('button', {
        type: 'button',
        className: 'sm ghost',
        textContent: 'cd + cmd',
        title: `Copy cd "${item.dir}" && ${item.cmd}`,
        onclick: () => copyText(`cd "${item.dir}" ; ${item.cmd}`, copyFullBtn),
      });
      row.append(copyFullBtn);
    }
    box.append(row);
  }
  host.prepend(box);
  while (host.children.length > 5) host.lastElementChild.remove();
  const ttl = (kind === 'err' || commands.length) ? 12000 : 4500;
  setTimeout(() => { if (box.isConnected) box.remove(); }, ttl);
}

export function extractResultCommands(results) {
  if (!Array.isArray(results)) return [];
  const cmds = [];
  for (const r of results) {
    if (!r || r.ok === false || r.status === 'error' || r.status === 'skipped') continue;
    const cmd = r.resumeCmd || formatResumeCmd(r.toProvider || r.targetProvider, r.targetId || r.id);
    if (cmd) cmds.push({ cmd, dir: r.resumeDir || r.targetCwd || r.cwd || '' });
  }
  return cmds.slice(0, 3);
}

export function summarizeBatchToast(r, fallbackMsg) {
  const results = Array.isArray(r.results) ? r.results : [];
  const failed = results.filter((x) => x && (x.ok === false || x.status === 'error'));
  const cmds = extractResultCommands(results);
  toast(r.message || fallbackMsg, failed.length && !cmds.length ? 'err' : 'ok', cmds);
  if (failed.length && cmds.length) {
    const firstErr = failed[0].error || failed[0].message || 'failed';
    toast(`${failed.length} item(s) failed: ${firstErr}`, 'err');
  }
}
