import { state } from '../state.js';

export async function withBusy(fn) {
  state.busy = true;
  const disabledNow = [];
  document.querySelectorAll('button').forEach((b) => {
    if (!b.disabled) {
      b.disabled = true;
      disabledNow.push(b);
    }
  });
  try {
    return await fn();
  } finally {
    state.busy = false;
    disabledNow.forEach((b) => {
      if (b.isConnected && !b.classList.contains('soon')) b.disabled = false;
    });
  }
}
