import type { IncomingMessage, ServerResponse } from 'node:http'
export function serveLocalAgents(req: IncomingMessage, res: ServerResponse, origin: URL, discover?: typeof discoverAgents): Promise<void>
export function discoverAgents(options?: { home?: string; envPath?: string; stat?: (path: string) => { isDirectory(): boolean; isFile(): boolean; mode: number } }): { id: string; name: string; cli: boolean; desktop: boolean; detected: boolean; capture_supported: boolean }[]
