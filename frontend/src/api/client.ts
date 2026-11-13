/**
 * The one way the frontend talks to the API (docs/04 §5.6, §13).
 *
 * Tokens
 *  - The ACCESS token (15 minutes) lives only in this module's memory: not localStorage, not
 *    sessionStorage, not a cookie. A script injected by an XSS bug can't read it from storage,
 *    and it disappears when the tab closes.
 *  - The REFRESH token is an httpOnly cookie the browser sends only to /api/v1/auth/*. This code
 *    never sees it.
 *  - After a page reload memory is empty, so the app calls refresh() once at start-up: if the
 *    cookie is still valid, the user is signed in again without typing anything.
 *
 * Refresh
 *  - A request that gets 401 token_expired (or session_invalid while we had a token) refreshes
 *    once and retries once.
 *  - Within a tab, concurrent requests share ONE refresh (a single in-flight promise).
 *  - Across tabs, refreshes are serialized with the Web Locks API: two tabs refreshing with the
 *    same cookie at the same moment would trip reuse detection otherwise. The second tab waits,
 *    then refreshes with the cookie the first tab just received.
 *  - If the server still answers refresh_superseded (another tab won a race within its 10-second
 *    grace window), the browser already has the newer cookie: retry once.
 */
import type { components } from './schema'

export type Problem = {
  type: string
  title: string
  status: number
  code: string
  detail?: string
  request_id?: string
  errors: { field: string; code: string; message: string }[]
  [key: string]: unknown
}

export class ApiError extends Error {
  readonly problem: Problem
  constructor(problem: Problem) {
    super(problem.detail ?? problem.title)
    this.problem = problem
  }
  get code(): string {
    return this.problem.code
  }
  get status(): number {
    return this.problem.status
  }
}

export type Realm = 'org' | 'platform'
export type AuthOut = components['schemas']['AuthOut']

const BASE = '/api/v1'
const REFRESH_PATH: Record<Realm, string> = { org: '/auth/refresh', platform: '/platform/auth/refresh' }
const LOGOUT_PATH: Record<Realm, string> = { org: '/auth/logout', platform: '/platform/auth/logout' }

const tokens: Record<Realm, string | null> = { org: null, platform: null }
const inFlight: Partial<Record<Realm, Promise<string | null>>> = {}
const listeners = new Set<() => void>()

export function getAccessToken(realm: Realm): string | null {
  return tokens[realm]
}

export function setAccessToken(realm: Realm, token: string | null): void {
  tokens[realm] = token
  listeners.forEach((fn) => fn())
}

/** Re-render subscribers (the auth provider) when a token appears or disappears. */
export function onTokenChange(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

async function toProblem(res: Response): Promise<Problem> {
  try {
    const body = (await res.json()) as Partial<Problem>
    if (body && typeof body.code === 'string') {
      // Rate limits send Retry-After (seconds): keep it so the message can say how long to wait.
      const retry = Number(res.headers.get('Retry-After'))
      return { errors: [], ...body, ...(retry > 0 ? { retry_after_seconds: retry } : {}) } as Problem
    }
  } catch {
    /* not JSON */
  }
  return { type: 'about:blank', title: res.statusText || 'Request failed', status: res.status, code: 'http_error', errors: [] }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

async function doRefresh(realm: Realm, attempt = 0): Promise<string | null> {
  const res = await fetch(BASE + REFRESH_PATH[realm], {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'X-Requested-With': 'hms' }, // CSRF header the server requires on cookie endpoints
  })
  if (res.ok) {
    const body = (await res.json()) as AuthOut
    setAccessToken(realm, body.access_token ?? null)
    return body.access_token ?? null
  }
  const problem = await toProblem(res)
  if (problem.code === 'refresh_superseded' && attempt < 2) {
    await sleep(250)
    return doRefresh(realm, attempt + 1)
  }
  setAccessToken(realm, null)
  return null
}

/** Get a new access token from the refresh cookie. Returns null when the user must sign in. */
export function refresh(realm: Realm): Promise<string | null> {
  const existing = inFlight[realm]
  if (existing) return existing
  const run = async () => {
    const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined
    return locks ? locks.request(`hms-refresh-${realm}`, () => doRefresh(realm)) : doRefresh(realm)
  }
  const promise = run().finally(() => {
    delete inFlight[realm]
  })
  inFlight[realm] = promise
  return promise
}

export type RequestOptions = { method?: string; body?: unknown; realm?: Realm; auth?: boolean; headers?: Record<string, string> }

/** Call the API. Throws ApiError with the server's problem details on any error. */
export async function api<T>(path: string, { method = 'GET', body, realm = 'org', auth = true, headers: extra = {} }: RequestOptions = {}): Promise<T> {
  const send = (token: string | null) =>
    fetch(BASE + path, {
      method,
      credentials: 'same-origin',
      headers: {
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...extra,
        ...(auth && token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })

  let res = await send(auth ? tokens[realm] : null)
  if (res.status === 401 && auth) {
    const problem = await toProblem(res.clone())
    const hadToken = tokens[realm] !== null
    if (problem.code === 'token_expired' || (problem.code === 'session_invalid' && hadToken)) {
      const fresh = await refresh(realm)
      if (fresh) res = await send(fresh)
    }
  }
  if (!res.ok) throw new ApiError(await toProblem(res))
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** Authenticated request that isn't JSON: multipart uploads, or binary downloads (restricted
 *  files are fetched with the Authorization header, never through a link, 04 §11.2). */
async function rawRequest(path: string, init: RequestInit, realm: Realm = 'org'): Promise<Response> {
  const send = (token: string | null) =>
    fetch(BASE + path, { ...init, credentials: 'same-origin', headers: { ...(init.headers ?? {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) } })
  let res = await send(tokens[realm])
  if (res.status === 401) {
    const problem = await toProblem(res.clone())
    if (problem.code === 'token_expired' || problem.code === 'session_invalid') {
      const fresh = await refresh(realm)
      if (fresh) res = await send(fresh)
    }
  }
  if (!res.ok) throw new ApiError(await toProblem(res))
  return res
}

export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const res = await rawRequest(path, { method: 'POST', body: form })
  return (await res.json()) as T
}

export async function apiBlob(path: string): Promise<Blob> {
  return (await rawRequest(path, { method: 'GET' })).blob()
}

export async function logout(realm: Realm): Promise<void> {
  try {
    await fetch(BASE + LOGOUT_PATH[realm], { method: 'POST', credentials: 'same-origin', headers: { 'X-Requested-With': 'hms' } })
  } finally {
    setAccessToken(realm, null)
  }
}
