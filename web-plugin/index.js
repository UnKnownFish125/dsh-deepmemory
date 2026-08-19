/**
 * Host half of the memory UI surface plugin.
 * Registers a same-origin proxy route `/mem-api/*` that forwards to the
 * local memory-server (localhost:6230). The browser half only talks to this
 * same-origin route, so no CORS or private-network policy applies.
 */
import http from 'node:http'
import fs from 'node:fs'

export const name = 'deepmemory'

export const inject = ['webServer']

const TARGET_HOST = 'localhost'
const TARGET_PORT = 6230
const PREFIX = '/mem-api'
const TOKEN_FILE = process.env.MEMORY_API_TOKEN_FILE || `${process.env.DSH_HOME || process.env.HOME}/.dsh-memory-api-token`

function readToken() {
  try { return fs.readFileSync(TOKEN_FILE, 'utf8').trim() } catch { return '' }
}

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
      delete headers.origin
      delete headers.authorization
      const token = readToken()
      if (token) headers.authorization = `Bearer ${token}`
      headers.host = `${TARGET_HOST}:${TARGET_PORT}`
      const upstream = http.request(
        {
          host: TARGET_HOST,
          port: TARGET_PORT,
          path: upstreamPath,
          method: req.method ?? 'GET',
          headers,
          timeout: 30000,
        },
        (upRes) => {
          res.writeHead(upRes.statusCode ?? 502, upRes.headers)
          upRes.pipe(res)
        },
      )
      upstream.on('timeout', () => upstream.destroy(new Error('memory-server request timeout')))
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
