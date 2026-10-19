import type { CoreClient } from './client'
export interface MemoryEngineState {
  enabled: boolean
  can_start: boolean
  state: 'UNAVAILABLE' | 'STOPPED' | 'STARTING' | 'RUNNING' | 'FAILED'
  operation_id: string | null
  started_at: string | null
  api_version: string | null
  error_code: string | null
}
export function memoryEngineEndpoints(client: CoreClient) {
  return {
    memoryEngineState: (signal?: AbortSignal) => client.get<MemoryEngineState>('/memory/engine', { signal }),
    startMemoryEngine: (signal?: AbortSignal) => client.post<MemoryEngineState>('/memory/engine/start', {}, { signal }),
  }
}
