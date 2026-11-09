/**
 * Local auto sign-in for a single-user machine (`GET /_local/session`).
 *
 * When the operator sets `P6_LOCAL_SIGNIN_CREDENTIAL` to the private browser
 * credential file (`config/credentials/browser-daily.json`), the dashboard's own
 * page may read that token once per load instead of asking the person to paste
 * it. Everything downstream is unchanged: the token stays in page memory only,
 * Core still checks its scopes, and the local Agent controls still compare its
 * hash with their pinned grant.
 *
 * Who can read it:
 * - only this server's own origin: the Host header must name the loopback
 *   address and port this server listens on (a DNS-rebinding page has another
 *   Host), and the browser must label the request same-origin;
 * - never a cross-site page (no CORS headers are sent, and the fetch-metadata
 *   check refuses it before the file is opened);
 * - other processes of the same OS user could read the file directly anyway.
 *   Other OS accounts on this machine can reach the loopback port; this mode is
 *   meant for a machine with one user.
 */
import { closeSync, constants, fstatSync, openSync, readFileSync } from 'node:fs'
import { isAbsolute } from 'node:path'

export const LOCAL_SIGNIN_ENV = 'P6_LOCAL_SIGNIN_CREDENTIAL'
const LOOPBACK_HOSTS = ['127.0.0.1', 'localhost', '[::1]']

function send(res, status, body) {
  res.statusCode = status
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.setHeader('cache-control', 'no-store')
  res.setHeader('x-content-type-options', 'nosniff')
  res.end(JSON.stringify(body))
}

/** The token from the credential file, or null when the file is not private and well formed. */
export function readLocalCredential(path) {
  if (typeof path !== 'string' || !isAbsolute(path) || path.includes('\0')) return null
  let fd
  try {
    fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK)
    const stat = fstatSync(fd)
    if (!stat.isFile() || stat.uid !== process.getuid() || (stat.mode & 0o077) || stat.size > 16384) return null
    const token = JSON.parse(readFileSync(fd, 'utf8'))?.token
    return typeof token === 'string' && /^[\x21-\x7e]{16,512}$/.test(token) ? token : null
  } catch {
    return null
  } finally {
    if (fd !== undefined) closeSync(fd)
  }
}

/** Whether the request comes from this server's own page. */
export function isOwnPage(req, port) {
  const host = req.headers.host
  if (typeof host !== 'string' || !LOOPBACK_HOSTS.some((name) => host === `${name}:${port}`)) return false
  if (req.headers['sec-fetch-site'] !== 'same-origin') return false
  const origin = req.headers.origin
  return origin === undefined || origin === `http://${host}`
}

export function serveLocalSession(req, res, { port, env = process.env } = {}) {
  const path = env[LOCAL_SIGNIN_ENV]
  if (!path) { send(res, 404, { error: 'local_signin_disabled' }); return }
  if (req.method !== 'GET') { res.setHeader('allow', 'GET'); send(res, 405, { error: 'method_not_allowed' }); return }
  if (!isOwnPage(req, port)) { send(res, 403, { error: 'local_signin_same_origin_only' }); return }
  const token = readLocalCredential(path)
  if (token === null) { send(res, 503, { error: 'local_signin_credential_unusable' }); return }
  send(res, 200, { token })
}
