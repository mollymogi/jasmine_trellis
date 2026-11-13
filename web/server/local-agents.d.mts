import type { IncomingMessage, ServerResponse } from 'node:http'
export const LOCAL_AGENTS_WRITE_ROUTE: RegExp
export function serveLocalAgents(req: IncomingMessage, res: ServerResponse, origin: URL, discover?: typeof discoverAgents, profile?: Record<string, any> | null): Promise<void>
export function discoverAgents(options?: { home?: string; envPath?: string; stat?: (path: string) => { isDirectory(): boolean; isFile(): boolean; mode: number } }): { id: string; name: string; cli: boolean; desktop: boolean; detected: boolean; capture_supported: boolean }[]
