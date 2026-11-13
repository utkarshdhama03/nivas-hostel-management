// D74 (updated 11 November): Student registration on the public pages, and how Student sign-in tells a
// registered student where the request stands. There is no status page.
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

type Call = { url: string; body: unknown }
let calls: Call[] = []
let loginAnswer: Response

const json = (status: number, body: object) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const SLOW = { timeout: 10_000 }
const HOSTEL = '0193a000-0000-7000-8000-000000000001'

beforeEach(() => {
  calls = []
  localStorage.clear()
  loginAnswer = json(401, { type: 'x', title: 'Wrong identifier or password', status: 401, code: 'invalid_credentials', errors: [] })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body
      calls.push({ url, body: body instanceof FormData ? Object.fromEntries(body.entries()) : body ? JSON.parse(String(body)) : undefined })
      if (url.includes('/public/organizations/closed-org/student-registration')) return json(200, { accepts_requests: false, organization_name: null, hostels: [] })
      if (url.includes('/student-registration/verify')) return new Response(null, { status: 204 })
      if (url.includes('/student-registration') && init?.method === 'POST') return json(202, { status: 'accepted' })
      if (url.includes('/student-registration'))
        return json(200, { accepts_requests: true, organization_name: 'Demo University Hostels', hostels: [{ id: HOSTEL, name: 'Aryabhata Boys Hostel', resident_type: 'boys' }] })
      if (url.includes('/auth/login')) return loginAnswer
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

test('an organization that does not take requests says so', { timeout: 20_000 }, async () => {
  await renderAt('/register/student')
  fireEvent.change(await screen.findByLabelText('Organization sign-in name', {}, SLOW), { target: { value: 'Closed-Org' } })
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByText(/doesn't take registration requests/, {}, SLOW)).toBeDefined()
  expect(calls.some((c) => c.url.includes('/public/organizations/closed-org/student-registration'))).toBe(true)
})

test('the form sends every field, the photo and the (empty) honeypot as one multipart request', { timeout: 20_000 }, async () => {
  await renderAt('/register/student')
  fireEvent.change(await screen.findByLabelText('Organization sign-in name', {}, SLOW), { target: { value: 'demo-uni' } })
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByRole('heading', { name: 'Register at Demo University Hostels' }, SLOW)).toBeDefined()
  fill('Hostel', HOSTEL)
  fill('Full name', 'Arjun Mehta')
  fill('Email', 'arjun@example.com')
  fill('Date of birth', '2007-04-01')
  fill("Parent or guardian's name", 'Rakesh Mehta')
  fill("Parent or guardian's phone", '+91 98765 22222')
  fireEvent.change(screen.getByLabelText('Your photo'), { target: { files: [new File(['jpeg'], 'me.jpg', { type: 'image/jpeg' })] } })
  fill('Password', 'my own long passphrase')
  fill('Password again', 'my own long passphrase typo')
  fireEvent.click(screen.getByRole('button', { name: 'Send request' }))
  expect(await screen.findByText('The two passwords are different.')).toBeDefined()
  fill('Password again', 'my own long passphrase')
  const trap = document.getElementById('reg-website') as HTMLInputElement // out of reach: no Tab stop, hidden from screen readers
  expect(trap.tabIndex).toBe(-1)
  expect(trap.closest('[aria-hidden="true"]')).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Send request' }))
  expect(await screen.findByRole('heading', { name: 'Check your email' }, SLOW)).toBeDefined()
  const sent = calls.find((c) => c.url.endsWith('/public/organizations/demo-uni/student-registration') && c.body)?.body as Record<string, unknown>
  expect(sent).toMatchObject({ hostel_id: HOSTEL, full_name: 'Arjun Mehta', email: 'arjun@example.com', gender: 'male', guardian_relationship: 'father', password: 'my own long passphrase' })
  expect(sent.photo).toBeInstanceOf(File)
  expect('confirm' in sent).toBe(false)
  expect('website' in sent).toBe(false) // the honeypot stays empty for people
})

test('the emailed link confirms the request and points to Student sign-in', { timeout: 20_000 }, async () => {
  await renderAt('/o/demo-uni/register/verify#token=abc123abc123')
  expect(await screen.findByText(/waiting for the administrator's approval/, {}, SLOW)).toBeDefined()
  expect(calls.find((c) => c.url.includes('/student-registration/verify'))?.body).toEqual({ token: 'abc123abc123' })
})

for (const [code, title, detail] of [
  ['registration_pending', "Your registration request is waiting for the administrator's approval", 'You can sign in here once it is approved.'],
  ['registration_rejected', 'Your registration request was rejected', "The administrator's reason: No rooms left"],
] as const) {
  test(`Student sign-in shows ${code} on the form`, { timeout: 20_000 }, async () => {
    loginAnswer = json(403, { type: 'x', title, status: 403, code, detail, errors: [] })
    await renderAt('/sign-in/student') // D80: email and password only
    fireEvent.change(await screen.findByLabelText('Email', {}, SLOW), { target: { value: 'arjun@example.com' } })
    fill('Password', 'my own long passphrase')
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    const status = await screen.findByTestId('registration-status', {}, SLOW)
    expect(status.textContent).toContain(title)
    expect(status.textContent).toContain(detail)
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeDefined() // still the sign-in form
  })
}
