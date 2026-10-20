import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, LoaderCircle, Play, RefreshCw } from 'lucide-react'
import { useSession } from '../app/session'
import type { MemoryEngineState } from '../api/memoryEngine'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

/** Only an explicit click starts a process; polling never replays POST. */
export function MemoryEngineStart({ onConnected, onStateChange }: { onConnected: () => void; onStateChange?: (state: MemoryEngineState | null) => void }): JSX.Element {
  const { endpoints } = useSession()
  const [engine, setEngine] = useState<MemoryEngineState | null>(null)
  const [error, setError] = useState(false)
  const [busy, setBusy] = useState(false)
  const sending = useRef(false)
  const alive = useRef(true)
  const connected = useRef<string | null>(null)
  const failed = useRef<string | null>(null)
  const callback = useRef(onConnected); callback.current = onConnected
  const stateCallback = useRef(onStateChange); stateCallback.current = onStateChange
  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await endpoints.memoryEngineState(signal)
      if (!alive.current || signal?.aborted) return
      setEngine(result); setError(false); stateCallback.current?.(result)
      if (result.state === 'RUNNING' && result.operation_id !== connected.current) {
        connected.current = result.operation_id; callback.current()
      }
      if (result.state === 'FAILED' && result.operation_id !== failed.current) {
        failed.current = result.operation_id; callback.current()
      }
    } catch { if (alive.current && !signal?.aborted) { setError(true); stateCallback.current?.(null) } }
  }, [endpoints])
  useEffect(() => { alive.current = true; const abort = new AbortController(); void reload(abort.signal); return () => { alive.current = false; abort.abort() } }, [reload])
  useEffect(() => {
    if (!engine || !['STARTING', 'RUNNING'].includes(engine.state) || error) return
    const abort = new AbortController(); const timer = setTimeout(() => void reload(abort.signal), engine.state === 'RUNNING' ? 5000 : 1000)
    return () => { clearTimeout(timer); abort.abort() }
  }, [engine, reload, error])
  async function start(): Promise<void> {
    if (sending.current || !engine?.enabled || !engine.can_start) return
    sending.current = true; setBusy(true); setError(false)
    try { const result = await endpoints.startMemoryEngine(); if (alive.current) { setEngine(result); stateCallback.current?.(result); if (result.state === 'RUNNING' && result.operation_id !== connected.current) { connected.current = result.operation_id; callback.current() } } }
    catch { if (alive.current) { setError(true); stateCallback.current?.(null) } }
    finally { sending.current = false; if (alive.current) setBusy(false) }
  }
  const starting = busy || engine?.state === 'STARTING'
  const running = !error && engine?.state === 'RUNNING'
  return <div className="mb-4 grid gap-2" data-testid="memory-engine-control">
    <div className="flex flex-wrap items-center gap-3">
      <Button disabled={running || starting || error || !engine?.enabled || !engine.can_start} onClick={() => void start()}>
        {starting ? <LoaderCircle className="animate-spin" /> : <Play />}
        {starting ? '正在启动引擎…' : engine?.state === 'FAILED' ? '重试启动引擎' : '一键启动引擎'}
      </Button>
      {running ? <Badge variant="success"><CheckCircle2 />引擎已连接</Badge> : null}
      {error ? <Button variant="outline" onClick={() => void reload()}><RefreshCw />重新读取启动状态</Button> : null}
    </div>
    <p className="text-xs text-muted-foreground" role={error || engine?.state === 'FAILED' ? 'alert' : 'status'}>
      {error ? '暂时无法确认启动状态，请重新读取；不会自动重复启动。' :
       engine?.state === 'FAILED' ? (engine.error_code === 'engine_port_in_use' ? '预设端口已被占用，没有接管或停止其他服务。' : engine.error_code === 'engine_start_timeout' ? '启动超时，本次启动的进程已停止；请检查配置后重试。' : engine.error_code === 'engine_exited' ? '引擎进程已退出，请检查本机运行配置后重试。' : '引擎未能启动，请检查本机运行配置后重试。') :
       running ? 'Hindsight 已在本机运行，无需重复启动。启动引擎不会自动开启记忆写入。' :
       starting ? '正在启动本机引擎并检查连接，请稍候。' :
       engine && !engine.enabled ? '当前部署尚未提供本机引擎启动配置。' :
       engine && !engine.can_start ? '当前为只读登录，启动引擎需要引擎管理权限。' :
       '启动并连接本机引擎；不会自动开启记忆写入或改动已有规则。'}
    </p>
  </div>
}
