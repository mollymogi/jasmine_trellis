import type { IncomingMessage, ServerResponse } from 'node:http'
export function loadInterpreterProfile(path?: string): Record<string, any> | null
export function readInterpreterState(profile: Record<string, any>, authorization?: string): any
export function selectInterpreterModel(profile: Record<string, any>, body: unknown, authorization?: string): any
export function serveLocalInterpreter(req: IncomingMessage, res: ServerResponse, origin: URL, profile?: Record<string, any> | null): Promise<void>
