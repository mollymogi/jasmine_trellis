export interface CaptureProfile { version: number; enabled: boolean; browser_token_sha256: string; python: string; installer: string; launcher: string; device_binding: string; receipts: string; grok_home: string; control_receipts: string }
export interface CaptureState { configured: boolean; can_manage: boolean; available: boolean; configuration_changed: boolean; last_message_at?: string | null; last_event_id?: string | null; last_error?: string | null }
export function loadCaptureProfile(path?: string): CaptureProfile | null
export function canManageCapture(profile: CaptureProfile | null, token?: string): boolean
export function captureStates(profile: CaptureProfile | null, authorization?: string): { grok: CaptureState }
export function controlCapture(profile: CaptureProfile, action: string): Promise<{status:string}>
