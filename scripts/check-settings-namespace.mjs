const baseUrl = process.env.DSH_URL || 'http://127.0.0.1:3091'
const target = new URL('/api/settings.describe', baseUrl)
const response = await fetch(target, {
  method: 'POST',
  headers: {
    'content-type': 'application/json',
    host: target.host,
    origin: baseUrl,
  },
  body: JSON.stringify({
    type: 'client-request',
    rpcId: 'deepmemory-settings-check',
    method: 'settings.describe',
    payload: {},
  }),
})

if (!response.ok) throw new Error(`settings.describe returned HTTP ${response.status}`)
const envelope = await response.json()
const namespaces = envelope?.result?.value?.namespaces
if (!Array.isArray(namespaces)) throw new Error('settings.describe returned no namespace list')
if (!namespaces.some((entry) => entry?.ns === 'deepmemory')) {
  throw new Error(`deepmemory namespace missing; found: ${namespaces.map((entry) => entry.ns).join(', ')}`)
}

console.log('PASS settings.describe exposes deepmemory')
