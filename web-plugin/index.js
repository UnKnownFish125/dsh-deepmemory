/**
 * Host half of the memory UI surface plugin.
 * Registers a same-origin proxy route `/mem-api/*` that forwards to the
 * local memory-server (127.0.0.1:6230). The browser half only talks to this
 * same-origin route, so no CORS or private-network policy applies.
 */
import http from 'node:http'

export const name = 'deepmemory'

export const inject = ['webServer']

const TARGET_HOST = '127.0.0.1'
const TARGET_PORT = 6230
const PREFIX = '/mem-api'

export function apply(ctx) {
  ctx.webServer.register({
    kind: 'prefix',
    path: PREFIX,
    handler: (req, res) => {
      let rel = req.url ?? ''
      if (rel.startsWith(PREFIX)) rel = rel.slice(PREFIX.length)
      if (!rel.startsWith('/')) rel = '/' + rel
      const upstreamPath = rel || '/v1/health'
      const headers = { ...req.headers }
      headers.host = `${TARGET_HOST}:${TARGET_PORT}`
      const upstream = http.request(
        {
          host: TARGET_HOST,
          port: TARGET_PORT,
          path: upstreamPath,
          method: req.method ?? 'GET',
          headers,
        },
        (upRes) => {
          res.writeHead(upRes.statusCode ?? 502, upRes.headers)
          upRes.pipe(res)
        },
      )
      upstream.on('error', (error) => {
        try {
          res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' })
          res.end(JSON.stringify({ error: String((error && error.message) || error) }))
        } catch {}
      })
      req.pipe(upstream)
    },
  })
}
