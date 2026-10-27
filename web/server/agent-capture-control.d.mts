export interface CaptureAdapter { python: string; installer: string; launcher?: string; device_binding: string; receipts: string; home: string }
export interface CaptureProfile { version: number; enabled: boolean; browser_token_sha256: string; control_receipts: string; python: string; installer: string; launcher: string; device_binding: string; receipts: string; grok_home: string; adapters?: Record<string, CaptureAdapter> }
export interface CaptureState { configured: boolean; can_manage: boolean; available: boolean; configuration_changed: boolean; client_hooks_disabled?: boolean; last_message_at?: string | null; last_event_id?: string | null; last_error?: string | null; memory_hint?: { available: boolean; enabled: boolean } }
export const CAPTURE_AGENTS: string[]
export const MEMORY_HINT_AGENTS: string[]
export function loadCaptureProfile(path?: string): CaptureProfile | null
export function canManageCapture(profile: CaptureProfile | null, token?: string): boolean
export function captureStates(profile: CaptureProfile | null, authorization?: string): Record<string,CaptureState>
export function controlCapture(profile: CaptureProfile, action: string, agent?: string, options?: { memoryHint?: boolean }): Promise<{status:string}>
