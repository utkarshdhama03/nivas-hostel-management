/**
 * The token handling in api/client.ts: memory-only access token, one refresh per tab even
 * for many concurrent 401s, retry after refresh_superseded, sign-out when refresh fails.
 */
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { api, ApiError, getAccessToken, refresh, setAccessToken } from './client'

type Call = { url: string; init: RequestInit }
let calls: Call[] = []

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } })
}
const problem = (status: number, code: string) => json(status, { type: `/problems/${code}`, title: code, status, code, errors: [] })

function mockFetch(handler: (url: string, init: RequestInit) => Response | Promise<Response>) {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init })
      return handler(url, init)
    }),
  )
}

beforeEach(() => setAccessToken('org', null))
afterEach(() => vi.unstubAllGlobals())

test('the access token is sent as a bearer header and kept only in memory', async () => {
  setAccessToken('org', 'token-1')
  mockFetch(() => json(200, { ok: true }))
  await api('/me')
  expect((calls[0]!.init.headers as Record<string, string>).Authorization).toBe('Bearer token-1')
  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
})

test('an expired token triggers ONE refresh for many concurrent requests, then each retries', async () => {
  setAccessToken('org', 'old')
  mockFetch((url, init) => {
    if (url.endsWith('/auth/refresh')) return json(200, { status: 'authenticated', access_token: 'new' })
    const auth = (init.headers as Record<string, string>).Authorization
    return auth === 'Bearer new' ? json(200, { ok: true }) : problem(401, 'token_expired')
  })
  await Promise.all([api('/me'), api('/me/notifications'), api('/auth/sessions')])
  expect(calls.filter((c) => c.url.endsWith('/auth/refresh'))).toHaveLength(1)
  expect(getAccessToken('org')).toBe('new')
  const refreshCall = calls.find((c) => c.url.endsWith('/auth/refresh'))!
  expect((refreshCall.init.headers as Record<string, string>)['X-Requested-With']).toBe('hms')
})

test('refresh_superseded (another tab won the race) is retried', async () => {
  let attempt = 0
  mockFetch(() => (++attempt === 1 ? problem(401, 'refresh_superseded') : json(200, { status: 'authenticated', access_token: 't2' })))
  expect(await refresh('org')).toBe('t2')
  expect(attempt).toBe(2)
})

test('a failed refresh signs the user out and the original error surfaces', async () => {
  setAccessToken('org', 'old')
  mockFetch((url) => (url.endsWith('/auth/refresh') ? problem(401, 'session_invalid') : problem(401, 'token_expired')))
  const err = await api('/me').catch((e: unknown) => e)
  expect(err).toBeInstanceOf(ApiError)
  expect((err as ApiError).code).toBe('token_expired')
  expect(getAccessToken('org')).toBeNull()
})

test('wrong credentials are not "refreshed away"', async () => {
  mockFetch(() => problem(401, 'invalid_credentials'))
  await expect(api('/o/x/auth/login', { method: 'POST', body: {}, auth: false })).rejects.toThrow()
  expect(calls).toHaveLength(1)
})

test('extra headers such as Idempotency-Key are sent', async () => {
  setAccessToken('org', 't')
  mockFetch(() => json(201, { id: 'p1' }))
  await api('/admin/payments', { method: 'POST', body: {}, headers: { 'Idempotency-Key': 'k-1' } })
  expect((calls[0]!.init.headers as Record<string, string>)['Idempotency-Key']).toBe('k-1')
})
