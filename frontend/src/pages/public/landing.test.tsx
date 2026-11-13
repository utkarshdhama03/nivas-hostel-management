// The public pages (D73): the landing page and its sidebar, and sign-in without an organization in the
// address. Rendered through the real router (App), with the API answered by a stub.
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { SITE } from '@/config/site'

type Call = { url: string; body: unknown }
let calls: Call[] = []

const json = (status: number, body: object) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const SLOW = { timeout: 10_000 }

beforeEach(() => {
  calls = []
  localStorage.clear()
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (url.includes('/public/organizations/')) return json(200, { slug: 'demo-uni', name: 'Demo University Hostels', status: 'active' })
      if (url.includes('/auth/login')) return json(200, { status: 'authenticated', access_token: 'x.y.z', token_type: 'bearer', expires_in: 900, user: { id: 'u', full_name: 'Asha', kind: url.includes('student') ? 'student' : 'staff' } })
      if (url.includes('/password-reset/request')) return json(202, { status: 'accepted' })
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

const sidebar = () => screen.getByRole('navigation', { name: 'Sign in and registration' })

test('the landing page: the name, the pitch, and the sections in order', { timeout: 20_000 }, async () => {
  await renderAt('/')
  expect(await screen.findByRole('heading', { level: 1, name: SITE.tagline }, SLOW)).toBeDefined()
  expect(SITE.tagline).toBe('Hostel management for colleges and hostel operators')
  const sections = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
  expect(sections).toEqual(['What it does', "Who it's for", 'How it works', 'Security and privacy', 'Contact'])
  for (const card of ['Rooms and beds', 'Fees and receipts', 'Leave requests', 'Complaints', 'Mess menu']) {
    expect(screen.getByRole('heading', { level: 3, name: card })).toBeDefined()
  }
  // Contact: the placeholders from the one config file, as links.
  expect(screen.getByRole('link', { name: SITE.contact.email }).getAttribute('href')).toBe(`mailto:${SITE.contact.email}`)
  expect(screen.getByRole('link', { name: SITE.contact.phone }).getAttribute('href')).toBe(`tel:${SITE.contact.phone.replace(/\s/g, '')}`)
  expect(screen.getByRole('contentinfo').textContent).toContain('Illustrations: original')
  expect(screen.getAllByRole('main')).toHaveLength(1)
})

test('one Administrator (D75, D76): the organizations card; no removed role or feature anywhere', { timeout: 20_000 }, async () => {
  await renderAt('/')
  const card = await screen.findByTestId('audience-organizations', {}, SLOW)
  expect(card.textContent?.trim()).toBe(
    'One administrator manages hostels, students, fees, payments, leave and complaints.',
  )
  const page = (screen.getByRole('main').textContent ?? '') + Array.from(document.querySelectorAll('svg[aria-label]'), (s) => s.getAttribute('aria-label')).join(' ')
  for (const word of [/add other administrators/i, /warden/i, /accountant/i, /security staff|security guard|guard/i, /gate/i, /attendance/i, /roll call/i, /\bQR\b/, /their room/i, /two-step/i, /authenticator/i, /analytics/i, /inventory/i, /\bstock\b/i]) { // D79: students don't see their room
    expect(page).not.toMatch(word)
  }
})

test('the sidebar: each entry has its own address, and the platform link is at the bottom', { timeout: 20_000 }, async () => {
  await renderAt('/')
  const nav = await screen.findByRole('navigation', { name: 'Sign in and registration' }, SLOW)
  const links = within(nav)
    .getAllByRole('link')
    .map((a) => [a.textContent?.trim(), a.getAttribute('href')])
  expect(links).toEqual([
    [SITE.productName.charAt(0) + SITE.productName, '/'],
    ['Student sign-in', '/sign-in/student'],
    ['Student registration', '/register/student'],
    ['Organization sign-in', '/sign-in/staff'],
    ['Register your organization', '/register'],
    ['Platform administration', '/platform/login'],
  ])
})

test('on a phone the sidebar folds behind a Menu button', { timeout: 20_000 }, async () => {
  await renderAt('/')
  const menu = await screen.findByRole('button', { name: 'Menu' }, SLOW)
  expect(menu.getAttribute('aria-expanded')).toBe('false')
  expect(document.getElementById('public-nav')?.className).toContain('hidden')
  fireEvent.click(menu)
  expect(menu.getAttribute('aria-expanded')).toBe('true')
  expect(document.getElementById('public-nav')?.className).toContain('block')
})

test('student sign-in asks for only the email and the password (D80)', { timeout: 20_000 }, async () => {
  await renderAt('/sign-in/student')
  expect(await screen.findByRole('heading', { level: 1, name: 'Student sign-in' }, SLOW)).toBeDefined()
  expect(screen.getByText('See your fees, leave and complaints.')).toBeDefined()
  expect(screen.queryByLabelText('Organization sign-in name')).toBeNull()
  expect(screen.queryByLabelText('Student ID or email')).toBeNull()
  expect(screen.getByRole('link', { name: 'Forgot password?' }).getAttribute('href')).toBe('/forgot-password')
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'asha@example.com' } })
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'a long passphrase' } })
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
  await waitFor(() => expect(calls.some((c) => c.url.includes('/auth/login'))).toBe(true), SLOW) // D87: no second step
  const login = calls.find((c) => c.url.includes('/auth/login'))
  expect(login?.url).toBe('/api/v1/auth/login') // no organization in the address
  expect(login?.body).toEqual({ email: 'asha@example.com', password: 'a long passphrase', portal: 'student' })
})

test('"Organization sign-in" asks for only the email and the password (D78)', { timeout: 20_000 }, async () => {
  await renderAt('/sign-in/staff')
  expect(await screen.findByRole('heading', { level: 1, name: 'Organization sign-in' }, SLOW)).toBeDefined()
  expect(screen.getByText("For the organization's administrator")).toBeDefined()
  expect(screen.queryByLabelText('Organization sign-in name')).toBeNull()
  expect(screen.queryByLabelText('Employee ID or email')).toBeNull()
  expect(screen.getByRole('link', { name: 'Forgot password?' }).getAttribute('href')).toBe('/forgot-password/staff')
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'admin@demo-university.example' } })
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'a long passphrase' } })
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
  await waitFor(() => expect(calls.some((c) => c.url.includes('/auth/login'))).toBe(true), SLOW) // D87: no second step
  const login = calls.find((c) => c.url.includes('/auth/login'))
  expect(login?.url).toBe('/api/v1/auth/login') // no organization in the address
  expect(login?.body).toEqual({ email: 'admin@demo-university.example', password: 'a long passphrase', portal: 'staff' })
})

test('Forgot password from Organization sign-in asks for only the email (D78)', { timeout: 20_000 }, async () => {
  await renderAt('/forgot-password/staff')
  expect(await screen.findByRole('heading', { name: 'Reset your password' }, SLOW)).toBeDefined()
  expect(screen.queryByLabelText('Organization sign-in name')).toBeNull()
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'admin@demo-university.example' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send code' }))
  await screen.findByLabelText('6-digit code from the email', {}, SLOW)
  const sent = calls.find((c) => c.url.includes('/password-reset/request'))
  expect(sent?.url).toBe('/api/v1/auth/password-reset/request')
  expect(sent?.body).toEqual({ email: 'admin@demo-university.example', portal: 'staff' })
})

test('the old address /o/:slug/login still works, with the organization fixed and an "Organization" tab', { timeout: 20_000 }, async () => {
  await renderAt('/o/demo-uni/login')
  expect(await screen.findByRole('heading', { name: 'Sign in to Demo University Hostels' }, SLOW)).toBeDefined()
  expect(screen.queryByLabelText('Organization sign-in name')).toBeNull()
  expect(screen.getByRole('tab', { name: 'Organization', selected: true })).toBeDefined()
  expect(screen.getByRole('tab', { name: 'Student' })).toBeDefined()
  expect(sidebarAbsent()).toBe(true)
})

function sidebarAbsent() {
  try {
    sidebar()
    return false
  } catch {
    return true
  }
}

test('D74: no activation help page any more; student sign-in points to registration and the emailed link', { timeout: 20_000 }, async () => {
  await renderAt('/activation')
  expect(await screen.findByRole('heading', { name: 'Page not found' }, SLOW)).toBeDefined()
  cleanup()
  await renderAt('/sign-in/student')
  expect((await screen.findByRole('link', { name: 'Ask to register' }, SLOW)).getAttribute('href')).toBe('/register/student')
  expect(screen.getByText('Got an activation email from your hostel? Open the link in it.')).toBeDefined()
  const footer = within(screen.getByRole('navigation', { name: 'Footer' }))
  expect(footer.getByRole('link', { name: 'Student registration' }).getAttribute('href')).toBe('/register/student')
  expect(footer.queryByRole('link', { name: 'Student account activation' })).toBeNull()
})
