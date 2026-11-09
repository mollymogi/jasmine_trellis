import type { IncomingMessage, ServerResponse } from 'node:http'
export const LOCAL_SIGNIN_ENV: string
export function readLocalCredential(path: string | undefined): string | null
export function isOwnPage(req: IncomingMessage, port: number | undefined): boolean
export function serveLocalSession(req: IncomingMessage, res: ServerResponse, options?: { port?: number; env?: Record<string, string | undefined> }): void
