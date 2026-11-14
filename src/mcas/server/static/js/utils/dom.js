export const $ = (id) => document.getElementById(id);

export function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null) continue;
      if (k === 'dataset' && typeof v === 'object') {
        Object.assign(node.dataset, v);
      } else if (k === 'style' && typeof v === 'object') {
        Object.assign(node.style, v);
      } else {
        node[k] = v;
      }
    }
  }
  for (const kid of kids) {
    if (kid !== undefined && kid !== null && kid !== false) node.append(kid);
  }
  return node;
}

export function svgIcon(pathD, size = 14) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  for (const d of (Array.isArray(pathD) ? pathD : [pathD])) {
    const p = document.createElementNS(ns, 'path');
    p.setAttribute('d', d);
    svg.append(p);
  }
  return svg;
}

// Safe DOM rendering for message text with fenced ```code``` blocks
export function renderMessageText(container, rawText) {
  container.replaceChildren();
  const text = String(rawText || '');
  if (!text) return;
  let pos = 0;
  while (pos < text.length) {
    const start = text.indexOf('```', pos);
    if (start === -1) {
      const tail = text.slice(pos).trim();
      if (tail) container.append(el('p', { textContent: tail }));
      break;
    }
    const before = text.slice(pos, start).trim();
    if (before) container.append(el('p', { textContent: before }));
    const nl = text.indexOf('\n', start + 3);
    const end = text.indexOf('```', start + 3);
    if (end === -1) {
      const rest = text.slice(start).trim();
      if (rest) container.append(el('p', { textContent: rest }));
      break;
    }
    const codeStart = (nl !== -1 && nl < end) ? nl + 1 : start + 3;
    const code = text.slice(codeStart, end).replace(/\n$/, '');
    container.append(el('pre', {}, el('code', { textContent: code })));
    pos = end + 3;
  }
}
