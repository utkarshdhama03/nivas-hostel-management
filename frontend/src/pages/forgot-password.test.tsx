// D77: forgot password by an emailed 6-digit code. Step 1 sends the organization and the identifier;
// step 2 sends the same identifier with the code and the new password. No reset link anywhere.
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

type Call = { url: string; body: unknown }
let calls: Call[] = []
let confirmAnswer: Response

const json = (status: number, body: object) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const SLOW = { timeout: 10_000 }

beforeEach(() => {
  calls = []
  localStorage.clear()
  confirmAnswer = new Response(null, { status: 204 })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (url.includes('/password-reset/request')) return json(202, { status: 'accepted' })
      if (url.includes('/password-reset/confirm')) return confirmAnswer
      return json(401, { type: 'x', title: 'Not signed in', status: 401, code: 'session_invalid', errors: [] })
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  localStorage.clear()
})

async function renderAt(url: string) {
  window.history.pushState({}, '', url)
  vi.resetModules()
  const { App } = await import('@/App')
  render(<App />)
}

const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })

test('email only (D80), then the emailed code and a new password', { timeout: 20_000 }, async () => {
  await renderAt('/forgot-password') // Student sign-in's (D80): only the email
  expect(await screen.findByRole('heading', { name: 'Reset your password' }, SLOW)).toBeDefined()
  expect(screen.queryByLabelText('Organization sign-in name')).toBeNull()
  fill('Email', 'asha@example.com')
  fireEvent.click(screen.getByRole('button', { name: 'Send code' }))
  expect(await screen.findByText(/we sent it a 6-digit code/, {}, SLOW)).toBeDefined()
  const request = calls.find((c) => c.url.includes('/password-reset/request'))
  expect(request?.url).toBe('/api/v1/auth/password-reset/request')
  expect(request?.body).toEqual({ email: 'asha@example.com', portal: 'student' })

  fill('6-digit code from the email', '123 456')
  fill('New password', 'a whole new passphrase')
  fireEvent.click(screen.getByRole('button', { name: 'Save password' }))
  expect(await screen.findByText(/All your devices were signed out/, {}, SLOW)).toBeDefined()
  const confirm = calls.find((c) => c.url.includes('/password-reset/confirm'))
  expect(confirm?.body).toEqual({ email: 'asha@example.com', portal: 'student', code: '123456', new_password: 'a whole new passphrase' })
  expect(JSON.stringify(calls)).not.toContain('token')
})

test('a wrong code shows the server message and keeps the form', { timeout: 20_000 }, async () => {
  confirmAnswer = json(400, { type: 'x', title: "That code didn't work", status: 400, code: 'invalid_reset_code', detail: 'Check the code in the email.', errors: [] })
  await renderAt('/o/demo-uni/forgot-password')
  // The organization comes from the address, and can still be changed.
  expect(((await screen.findByLabelText('Organization sign-in name', {}, SLOW)) as HTMLInputElement).value).toBe('demo-uni')
  fill('Employee ID, Student ID or email', 'E-0001')
  fireEvent.click(screen.getByRole('button', { name: 'Send code' }))
  await screen.findByLabelText('6-digit code from the email', {}, SLOW)
  fill('6-digit code from the email', '000000')
  fill('New password', 'a whole new passphrase')
  fireEvent.click(screen.getByRole('button', { name: 'Save password' }))
  expect(await screen.findByText(/That code didn't work/, {}, SLOW)).toBeDefined()
  expect(screen.getByLabelText('6-digit code from the email')).toBeDefined()
})

test('the reset-link page is gone', { timeout: 20_000 }, async () => {
  await renderAt('/o/demo-uni/reset-password#token=abc')
  expect(await screen.findByText(/not found/i, {}, SLOW)).toBeDefined()
  expect(screen.queryByText('Choose a new password')).toBeNull()
})
