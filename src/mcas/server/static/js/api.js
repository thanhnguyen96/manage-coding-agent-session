export async function api(path, body) {
  const authToken = typeof token !== 'undefined' ? token : '';
  const res = await fetch('/api/' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-Mcas-Token': authToken, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({ error: 'Invalid response from mcas server.' }));
  if (!res.ok) {
    const err = new Error(data.error || res.statusText || 'Request failed');
    err.httpStatus = res.status;
    throw err;
  }
  return data;
}
