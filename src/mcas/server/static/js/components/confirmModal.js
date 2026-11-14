import { $ } from '../utils/dom.js';

export function askConfirm(title, message, okLabel = 'Confirm', isDanger = true) {
  return new Promise((resolve) => {
    $('cfTitle').textContent = title;
    $('cfMessage').textContent = message;
    const okBtn = $('cfOkBtn');
    okBtn.textContent = okLabel;
    okBtn.className = isDanger ? 'danger' : 'primary';
    $('confirmModal').hidden = false;
    okBtn.focus();

    const finish = (val) => {
      $('confirmModal').hidden = true;
      okBtn.onclick = null;
      $('cfCancelBtn').onclick = null;
      $('cfCloseBtn').onclick = null;
      $('confirmModal').onclick = null;
      document.removeEventListener('keydown', onKey);
      resolve(val);
    };
    const onKey = (e) => { if (e.key === 'Escape') finish(false); };
    okBtn.onclick = () => finish(true);
    $('cfCancelBtn').onclick = () => finish(false);
    $('cfCloseBtn').onclick = () => finish(false);
    $('confirmModal').onclick = (e) => { if (e.target === $('confirmModal')) finish(false); };
    document.addEventListener('keydown', onKey);
  });
}
