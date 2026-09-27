// Thin fetch wrapper — every page calls real /api/... endpoints.
// Nothing here is hard-coded sample data.
export async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  let body = null
  try {
    body = await res.json()
  } catch {
    // no body
  }
  if (!res.ok) {
    const err = new Error((body && body.detail) || `${res.status} ${res.statusText}`)
    err.status = res.status
    err.body = body
    throw err
  }
  return body
}
