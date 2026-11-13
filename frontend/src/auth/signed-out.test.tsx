// Q91 (decided: keep the strict refresh rule): when a session ends, the sign-in page must say so
// clearly, instead of silently showing an empty sign-in form.
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

// The router is created when App is imported, so each test sets the URL first, then imports it fresh.
async function renderAppAt(url: string, state: object = {}) {
  window.history.pushState(state, '', url)
  vi.resetModules()
  const { App } = await import('@/App')
  render(<App />)
}

// Importing the whole app takes over a second when the other test files run at the same time, so
// these waits allow more than the default second (the test failed in the full run, passed alone).
const SLOW = { timeout: 10_000 }

const json = (status: number, body: object) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  localStorage.setItem('hms.lastOrg', 'demo-university') // this browser was signed in before
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url.includes('/auth/refresh')) return json(401, { type: 'x', title: 'Session ended', status: 401, code: 'session_invalid', errors: [] })
      if (url.includes('/public/organizations/')) return json(200, { slug: 'demo-university', name: 'Demo University' })
      return json(401, { type: 'x', title: 'Not signed in', status: 401, code: 'session_invalid', errors: [] })
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  localStorage.clear()
})

test('an ended session lands on the sign-in page with a clear message', { timeout: 20_000 }, async () => {
  await renderAppAt('/admin/students')
  expect(await screen.findByText('Your session has ended. Please sign in again.', {}, SLOW)).toBeDefined()
  expect(await screen.findByRole('heading', { name: 'Sign in to Demo University' }, SLOW)).toBeDefined()
})
