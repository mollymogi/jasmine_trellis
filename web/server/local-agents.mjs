/** Discovery is read-only. A separate POST may enable/disable the fixed Grok
 * adapter only under a private operator profile and a pinned management key. */
import http from 'node:http'
import { loadCaptureProfile, captureStates, canManageCapture, controlCapture } from './agent-capture-control.mjs'
import { statSync } from 'node:fs'
import { homedir } from 'node:os'
import { delimiter, join } from 'node:path'

const CATALOG = [
  { id: 'codex', name: 'Codex', command: 'codex', apps: ['Codex.app'], adapter: true },
  { id: 'claude', name: 'Claude', command: 'claude', apps: ['Claude.app'] },
  { id: 'grok', name: 'Grok', command: 'grok', dirs: ['.grok/bin'], adapter: true },
  { id: 'opencode', name: 'OpenCode', command: 'opencode', dirs: ['.opencode/bin'], apps: ['OpenCode.app'] },
  { id: 'gemini', name: 'Gemini CLI', command: 'gemini' },
  { id: 'aider', name: 'Aider', command: 'aider' },
  { id: 'cursor', name: 'Cursor', command: 'cursor', apps: ['Cursor.app'] },
  { id: 'windsurf', name: 'Windsurf', command: 'windsurf', apps: ['Windsurf.app'] },
]
export function discoverAgents({ home = homedir(), envPath = process.env.PATH ?? '', stat = statSync } = {}) {
  const dirs = [...new Set([...envPath.split(delimiter).filter(p => p.startsWith('/')), '/opt/homebrew/bin', '/usr/local/bin', join(home, '.local/bin'), join(home, '.npm-global/bin')])]
  const exists = (path, app = false) => {
    try { const s = stat(path); return app ? s.isDirectory() : s.isFile() && (s.mode & 0o111) !== 0 } catch { return false }
  }
  return CATALOG.map(agent => {
    const cli = [...dirs, ...(agent.dirs ?? []).map(dir => join(home, dir))].some(dir => exists(join(dir, agent.command)))
    const desktop = (agent.apps ?? []).some(app => ['/Applications', join(home, 'Applications')].some(dir => exists(join(dir, app), true)))
    return { id: agent.id, name: agent.name, cli, desktop, detected: cli || desktop, capture_supported: agent.adapter === true }
  })
}
function send(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' })
  res.end(JSON.stringify(body))
}
export async function serveLocalAgents(req, res, origin, discover = discoverAgents, profile = loadCaptureProfile()) {
  let host
  try { host = new URL(`http://${req.headers.host}`).hostname } catch { /* rejected below */ }
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(host)
    || req.headers['sec-fetch-site'] === 'cross-site'
    || (req.headers.origin && req.headers.origin !== `http://${req.headers.host}`)) {
    send(res, 403, { error: 'local_origin_required' }); return
  }
  const path=new URL(req.url,'http://local.invalid').pathname
  const write=path==='/_local/agents/grok/capture'
  if(req.method!==(write?'POST':'GET')) {send(res,405,{error:'method_not_allowed'});return}
  if(write&&req.headers.origin!==`http://${req.headers.host}`){send(res,403,{error:'same_origin_required'});return}
  const token = req.headers.authorization
  if (!token?.startsWith('Bearer ')) { send(res, 401, { error: 'authentication_required' }); return }
  // Core validates the caller's objects:read grant; no local credential or key is added.
  const status = await new Promise(resolve => {
    const check = http.get(new URL('/v1/projects?limit=1', origin), { headers: { authorization: token } }, reply => {
      reply.resume(); resolve(reply.statusCode ?? 502)
    })
    check.setTimeout(3000, () => check.destroy())
    check.on('error', () => resolve(502))
  })
  if (status !== 200) { send(res, status === 401 || status === 403 ? status : 502, { error: 'core_authorization_failed' }); return }
  if(write){
    if(!canManageCapture(profile,token)){send(res,403,{error:'capture_management_not_granted'});return}
    try {
      let body='';for await(const chunk of req){body+=chunk;if(Buffer.byteLength(body)>1024){send(res,413,{error:'request_too_large'});return}}
      const value=JSON.parse(body);if(!value||Object.keys(value).length!==1||!['enable','disable'].includes(value.action)){send(res,400,{error:'invalid_capture_action'});return}
      const result=await controlCapture(profile,value.action);send(res,200,{...result,capture:captureStates(profile,token).grok});return
    }catch{send(res,502,{error:'capture_install_failed'});return}
  }
  const states=captureStates(profile,token)
  send(res, 200, { agents: discover().map(agent=>({...agent,...(states[agent.id]?{capture:states[agent.id]}:{})})), checked_at: new Date().toISOString(), scope: 'dashboard_host_known_locations' })
}
