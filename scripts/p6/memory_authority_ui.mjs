/** Isolated real Chrome gate. Descriptor on stdin; no bearer/profile persisted. */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import net from 'node:net'
import { Cdp, CHROME_CANDIDATES, waitForCdp, httpJson, evaluate, waitFor, clickByText, typeInto } from './browser_trace.mjs'
let input = ''; for await (const chunk of process.stdin) input += chunk
const config = JSON.parse(input)
const report = { status: 'RUNNING', phase: config.phase, checks: [], network: [], core_operations: [], network_requests: [] }
let chrome, cdp, profile
function check(name, ok, detail) { report.checks.push({ name, passed: !!ok, detail }); if (!ok) throw new Error(name) }
async function freePort() { const s = net.createServer(); await new Promise(r => s.listen(0, '127.0.0.1', r)); const p = s.address().port; await new Promise(r => s.close(r)); return p }
async function shot(name) {
  await evaluate(cdp, name.includes('expanded')
    ? `document.querySelector('[data-testid="memory-authority"]')?.scrollIntoView({block:'start'})`
    : `window.scrollTo(0,0)`)
  const s = await cdp.send('Page.captureScreenshot', { format: 'png' })
  await writeFile(join(config.out, name + '.png'), Buffer.from(s.data, 'base64'))
}
async function openComparison() {
  const before = report.network_requests.filter(r => r.method !== 'GET').length
  await clickByText(cdp, 'button', '查看判定依据')
  check('comparison_details_expanded', await evaluate(cdp, `!!document.querySelector('[data-testid="memory-authority-details"]') && !!document.querySelector('[role="dialog"]')`))
  check('expand_does_not_write', before === report.network_requests.filter(r => r.method !== 'GET').length)
}

async function navigate(path, token = config.readToken) {
  await cdp.send('Page.navigate', { url: config.ui + path })
  await waitFor(cdp, `!!document.querySelector('input[type="password"]') || !!document.querySelector('[data-testid="memory-authority"]') || !!document.querySelector('[data-testid="memory-authority-filters"]')`)
  if (await evaluate(cdp, `!!document.querySelector('input[type="password"]')`)) {
    await typeInto(cdp, 'input[type="password"]', token); await clickByText(cdp, 'button', '使用此 token')
    await waitFor(cdp, `!document.querySelector('input[type="password"]')`)
  }
}
async function item(id, status, token) {
  await navigate('/#/memory/items/' + id, token)
  await waitFor(cdp, `document.querySelector('[data-testid="memory-authority-status"]')?.dataset.status === ${JSON.stringify(status)}`)
  check('item_' + status, (await evaluate(cdp, `document.querySelector('[data-testid="memory-authority-status"]').dataset.status`)).includes(status))
}
async function core(method, path, body) {
  const r = await fetch(config.core + path, { method, headers: { Authorization: 'Bearer ' + config.setupToken, 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) })
  const value = await r.json(); report.core_operations.push({ method, path, status: r.status, request_id: r.headers.get('x-request-id'), response: value }); check('core_operation_' + path, r.ok); return value
}
try {
  const binary = CHROME_CANDIDATES.find(p => p && existsSync(p)); check('Chrome_available', binary)
  profile = await mkdtemp(join(tmpdir(), 'p6-memory-authority-')); const port = await freePort()
  chrome = spawn(binary, [`--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, '--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-background-networking', '--disable-gpu', 'about:blank'], { stdio: 'ignore' })
  await waitForCdp(port); const target = await httpJson(`http://127.0.0.1:${port}/json/new?about:blank`, 'PUT'); cdp = new Cdp(target.webSocketDebuggerUrl); await cdp.connect()
  report.browser = await cdp.send('Browser.getVersion'); await cdp.send('Page.enable'); await cdp.send('Runtime.enable'); await cdp.send('Network.enable')
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1200, deviceScaleFactor: 1, mobile: false })
  cdp.onEvent(m => { if (m.method === 'Network.responseReceived') { const r=m.params.response; if (new URL(r.url).pathname.startsWith('/v1/')) report.network.push({ path: new URL(r.url).pathname, status: r.status, request_id: Object.entries(r.headers).find(([k]) => k.toLowerCase()==='x-request-id')?.[1] }) } })
  cdp.onEvent(m => { if (m.method === 'Network.requestWillBeSent') { const r=m.params.request; if (new URL(r.url).pathname.startsWith('/v1/')) report.network_requests.push({ method:r.method, path:new URL(r.url).pathname }) } })
  if (config.phase === 'live') {
    await item(config.live.memory, 'VERSION_MATCH')
    check('default_details_closed', await evaluate(cdp, `!document.querySelector('[role="dialog"]') && !document.querySelector('[data-testid="memory-authority-details"]') && !document.querySelector('[data-testid="memory-item-record"]')`))
    check('technical_metadata_hidden_by_default', await evaluate(cdp, `!document.body.innerText.includes('same_linked_version_only') && !document.body.innerText.includes('content_sha256')`))
    await shot('initial-version-match')
    const before = await core('GET', '/v1/rules/' + config.live.rule)
    const event = await core('POST', '/v1/events', { event_type: 'user.prompt', source_system: 'labelled-p6-isolated-fixture', host_id: config.host, project_id: config.project, payload: { text: 'SYNTHETIC fixture: change decision to engine B' } })
    await core('POST', '/v1/rules/' + config.live.rule + '/supersede', { expected_revision: before.rule.revision, host_id: config.host, kind: 'DECISION', severity: 'HARD', enforcement: 'CONTEXT', content: 'SYNTHETIC current decision: use engine B', matcher: {}, scope: { kind: 'project', project_id: config.project }, origin_event_id: event.event.event_id })
    await clickByText(cdp, 'button', '刷新')
    await waitFor(cdp, `document.querySelector('[data-testid="memory-authority-status"]').dataset.status.includes('VERSION_CHANGED')`)
    check('reload_replaces_old_match', !(await evaluate(cdp, `document.querySelector('[data-testid="memory-authority"]').textContent.includes('VERSION_MATCH')`)))
    await shot('superseded-memory-warning')
    const originalDark = await evaluate(cdp, "document.documentElement.classList.contains('dark')")
    for (const dark of [false, true]) {
      await evaluate(cdp, `document.documentElement.classList.toggle('dark', ${dark})`)
      const geometry = await evaluate(cdp, `(() => { const root=document.querySelector('[data-slot="detail-layout"]'); const m=root.querySelector('[data-slot="detail-layout-main"]').getBoundingClientRect(); const a=root.querySelector('[data-slot="detail-layout-aside"]').getBoundingClientRect(); return {main:m.width, aside:a.width, sideBySide:a.x>m.x, width:root.clientWidth, scrollWidth:root.scrollWidth}; })()`)
      check((dark ? 'dark' : 'light') + '_dashboard_main_emphasis', geometry.sideBySide && geometry.main > geometry.aside * 1.6 && geometry.scrollWidth <= geometry.width + 1, geometry)
      await shot('dashboard-' + (dark ? 'dark' : 'light'))
    }
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 1000, deviceScaleFactor: 1, mobile: true })
    await evaluate(cdp, 'new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
    const mobileLayout = await evaluate(cdp, `(() => { const root=document.querySelector('[data-slot="detail-layout"]'); const m=root.querySelector('[data-slot="detail-layout-main"]').getBoundingClientRect(); const a=root.querySelector('[data-slot="detail-layout-aside"]').getBoundingClientRect(); return {ordered:a.y>=m.bottom-1, rootWidth:root.clientWidth, rootScrollWidth:root.scrollWidth, pageWidth:innerWidth, pageScrollWidth:document.documentElement.scrollWidth}; })()`); check('mobile_dashboard_no_horizontal_overflow', mobileLayout.ordered && mobileLayout.rootScrollWidth<=mobileLayout.rootWidth+1 && mobileLayout.pageScrollWidth<=mobileLayout.pageWidth+1, mobileLayout)
    await shot('dashboard-mobile')
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1200, deviceScaleFactor: 1, mobile: false })
    await evaluate(cdp, `document.documentElement.classList.toggle('dark', ${originalDark})`)
    await openComparison()
    check('side_by_side_versions', (await evaluate(cdp, `document.querySelector('[data-testid="memory-authority-versions"]').textContent`)).includes('v1 → 当前记录 v2'))
    check('old_and_current_text', await evaluate(cdp, `document.body.textContent.includes('engine A') && document.body.textContent.includes('engine B')`))
    check('no_HTML_execution', await evaluate(cdp, `globalThis.p6Injected === undefined && !document.querySelector('[data-testid="memory-authority"] script')`))
    await shot('superseded-memory-expanded')
    await clickByText(cdp, '[data-testid="memory-authority"] a', '读取最新 Rule / 版本历史')
    await waitFor(cdp, `location.hash.startsWith('#/rules') && !document.querySelector('[data-testid="memory-authority"]') && document.body.textContent.includes('engine B') && document.body.textContent.includes(${JSON.stringify(config.live.rule)})`)
    check('rule_deeplink_current_truth', true); await shot('current-rule-readback')
    await item(config.live.memory, 'VERSION_CHANGED')
    await openComparison()
    await clickByText(cdp, '[data-testid="memory-authority"] a', '历史来源 Event')
    await waitFor(cdp, `location.hash === '#/event/' + ${JSON.stringify(config.live.origin)} && !document.querySelector('[data-testid="memory-authority"]') && document.body.textContent.includes(${JSON.stringify(config.live.origin)})`)
    check('historical_source_deeplink', true)
    await cdp.send('Page.reload'); await waitFor(cdp, `!!document.querySelector('input[type="password"]')`)
    await typeInto(cdp, 'input[type="password"]', config.readToken); await clickByText(cdp, 'button', '使用此 token')
    await waitFor(cdp, `document.body.textContent.includes(${JSON.stringify(config.live.origin)}) && !document.querySelector('input[type="password"]')`)
    check('source_deeplink_reload', true)
  } else {
    await item(config.retired.memory, 'ORIGIN_RETIRED'); await shot('retired-memory-warning')
    await item(config.unlinked, 'UNVERIFIABLE'); await shot('unlinked-memory-unknown')
    await navigate('/#/memory/traces/' + config.trace)
    await waitFor(cdp, `!!document.querySelector('[data-testid="memory-authority-filters"] [role="alert"]')`)
    check('trace_details_closed', await evaluate(cdp, `!document.querySelector('[data-testid="memory-filter-details"]')`))
    await shot('historical-recall-summary')
    await clickByText(cdp, 'button', '查看过滤记录')
    check('trace_displays_both_refusal_reasons', await evaluate(cdp, `document.body.textContent.includes('superseded_decision') && document.body.textContent.includes('retired_decision')`))
    check('trace_not_misrepresented_as_current', await evaluate(cdp, `document.querySelector('[data-testid="memory-authority-filters"]').textContent.includes('召回当时')`))
    await shot('historical-recall-refusals')
    await clickByText(cdp, '[data-testid="memory-authority-filters"] a', '查看历史 Memory 与当前 Authority')
    await waitFor(cdp, `!!document.querySelector('[data-testid="memory-authority-status"]')`)
    check('trace_to_memory_link', true)
    await cdp.send('Page.reload'); await waitFor(cdp, `!!document.querySelector('input[type="password"]')`)
    await typeInto(cdp, 'input[type="password"]', config.limitedToken); await clickByText(cdp, 'button', '使用此 token')
    await waitFor(cdp, `!!document.querySelector('[data-testid="memory-authority-status"]')`)
    check('limited_key_explicit_unavailable', (await evaluate(cdp, `document.querySelector('[data-testid="memory-authority-status"]').dataset.status`)).includes('UNAVAILABLE'))
    check('limited_key_no_current_rule_body', await evaluate(cdp, `!document.querySelector('[data-testid="memory-authority-versions"]') && !document.querySelector('[data-testid="memory-authority"]').textContent.includes('engine B')`))
    await shot('authority-scope-unavailable')
  }
  check('proxy_item_read_observed', report.network.some(r => r.path.startsWith('/v1/memory/items/') && r.status===200 && r.request_id))
  check('no_persistent_browser_credentials', await evaluate(cdp, `(async()=>localStorage.length===0 && sessionStorage.length===0 && (await indexedDB.databases()).length===0)()`))
  check('no_bearer_in_report', !JSON.stringify(report).includes(config.readToken) && !JSON.stringify(report).includes(config.setupToken) && !JSON.stringify(report).includes(config.limitedToken))
  report.status = 'PASS_REAL_CORE_CHROME_WITH_LABELLED_ENGINE_DOUBLE'
} catch(e) { report.status='FAIL'; report.error=String(e.stack??e); if(cdp) await shot('failure-'+config.phase).catch(()=>{}) }
finally { cdp?.close(); if(chrome) { chrome.kill('SIGTERM'); await new Promise(r=>{ if(chrome.exitCode!==null)r(); else {chrome.once('exit',r);setTimeout(()=>{chrome.kill('SIGKILL');r()},3000).unref()} }) } if(profile)await rm(profile,{recursive:true,force:true}); await writeFile(join(config.out, 'browser-'+config.phase+'.json'),JSON.stringify(report,null,2)+'\n') }
console.log(JSON.stringify({status:report.status,checks:report.checks.length,error:report.error}));process.exitCode=report.status.startsWith('PASS')?0:1
