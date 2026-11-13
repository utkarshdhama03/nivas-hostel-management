// The readable audit log (D89): entries grouped by day (Today, Yesterday, then dates) with 12-hour times,
// a chip per category, System marked as automatic, details on demand, links, filters and Load more.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { setAccessToken } from '@/api/client'

import { AuditPage } from './AuditPage'

const entry = (id: string, occurred_at: string, extra: object = {}) => ({
  id,
  occurred_at,
  hostel_id: null,
  actor_type: 'user',
  actor_user_id: 'u-admin',
  actor_roles: ['super_admin'],
  action: 'refund.self_approved_sole_administrator',
  entity_type: 'refund_request',
  entity_id: 'rr-1',
  changed_fields: [],
  before_values: null,
  after_values: null,
  request_id: null,
  ip_address: null,
  category: 'refunds',
  sentence: 'Kavita Deshpande approved their own refund request of ₹2,500 for Saurabh Parikh',
  actor_name: 'Kavita Deshpande',
  actor_kind: 'administrator',
  summary: null,
  details: [],
  student_id: null,
  student_name: null,
  link_label: null,
  link_to: null,
  ...extra,
})

// Now: 13-11-2026 11:30 IST. 19:18 UTC on the 12th is 12:48 AM IST on the 13th: "Today".
const PAGE1 = {
  items: [
    entry('a3', '2026-11-12T19:18:00Z', {
      student_id: 'st-1',
      student_name: 'Saurabh Parikh',
      link_label: 'Open student',
      link_to: '/admin/students/st-1',
      summary: 'Status: approved · Self-approved: Yes',
      details: [
        { field: 'status', label: 'Status', before: null, after: 'approved' },
        { field: 'self_approved', label: 'Self-approved', before: null, after: 'Yes' },
      ],
    }),
    entry('a2', '2026-11-12T08:00:00Z', {
      actor_type: 'system',
      actor_user_id: null,
      actor_kind: 'system',
      actor_name: 'System',
      category: 'registration',
      action: 'student_request.submitted',
      sentence: 'Amit Sharma sent a registration request',
    }),
  ],
  next_cursor: 'a2',
}
const PAGE2 = {
  items: [entry('a1', '2026-11-01T04:30:00Z', { category: 'notices', action: 'mess_menu.saved', sentence: 'Kavita Deshpande saved the mess menu for Aryabhata Boys Hostel' })],
  next_cursor: null,
}

let urls: string[] = []
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  urls = []
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-13T06:00:00Z'))
  setAccessToken('org', 'test-token')
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      urls.push(url)
      if (url.endsWith('/me')) return json({ organization: { timezone: 'Asia/Kolkata' }, permissions: [] })
      if (url.includes('/admin/staff')) return json({ items: [{ user_id: 'u-admin', full_name: 'Kavita Deshpande' }] })
      if (url.includes('/admin/audit-logs')) return json(url.includes('cursor=a2') ? PAGE2 : PAGE1)
      return json({})
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  setAccessToken('org', null)
})

function renderPage() {
  const router = createMemoryRouter([{ path: '/admin/audit', element: <AuditPage /> }, { path: '/admin/students/:id', element: <p>Student page</p> }], {
    initialEntries: ['/admin/audit'],
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

function nth(list: HTMLElement[], i: number): HTMLElement {
  const el = list[i]
  if (!el) throw new Error(`no element ${i}`)
  return el
}

const lastAuditUrl = () => [...urls].reverse().find((u) => u.includes('/admin/audit-logs')) ?? ''

test('entries are sentences grouped by day, with 12-hour times and category chips', async () => {
  renderPage()
  expect(await screen.findByRole('heading', { name: 'Today' })).toBeDefined()
  expect(screen.getByRole('heading', { name: 'Yesterday' })).toBeDefined()
  const [today, yesterday] = [nth(screen.getAllByRole('region'), 0), nth(screen.getAllByRole('region'), 1)]
  expect(within(today).getByText('12:48 AM')).toBeDefined()
  expect(within(today).getByText('Kavita Deshpande approved their own refund request of ₹2,500 for Saurabh Parikh')).toBeDefined()
  expect(within(today).getByText('Refunds')).toBeDefined()
  expect(within(yesterday).getByText('1:30 PM')).toBeDefined()
  expect(within(yesterday).getByText('Registration requests')).toBeDefined()
  // System is named, and marked as done automatically
  expect(within(yesterday).getByText('System')).toBeDefined()
  expect(within(yesterday).getByText('Done automatically')).toBeDefined()
  expect(within(today).queryByText('Done automatically')).toBeNull()
  // newest first, then Load more brings the older page with a dated heading
  fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
  expect(await screen.findByRole('heading', { name: '01-11-2026' })).toBeDefined()
  expect(within(screen.getByRole('region', { name: '01-11-2026' })).getByText('Notices')).toBeDefined()
  expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull()
})

test('a summary inline, the before and after behind Details, and a link to the record', async () => {
  const router = renderPage()
  const first = nth(await screen.findAllByTestId('audit-entry'), 0)
  expect(within(first).getByText('Status: approved · Self-approved: Yes')).toBeDefined()
  const details = within(first).getByRole('button', { name: 'Details' })
  expect(details.getAttribute('aria-expanded')).toBe('false')
  fireEvent.click(details)
  expect(details.getAttribute('aria-expanded')).toBe('true')
  const rows = within(first).getAllByTestId('audit-detail')
  expect(rows.map((r) => r.textContent)).toEqual(['Statusapproved', 'Self-approvedYes']) // new values: no "before"
  expect(within(first).getByLabelText('What changed')).toBeDefined()
  // an entry with nothing changed has no Details button
  const second = nth(screen.getAllByTestId('audit-entry'), 1)
  expect(within(second).queryByRole('button', { name: 'Details' })).toBeNull()
  fireEvent.click(within(first).getByRole('link', { name: 'Open student' }))
  await waitFor(() => expect(router.state.location.pathname).toBe('/admin/students/st-1'))
})

test('filters: name, category, dates, who did it, and the action code under Advanced', async () => {
  renderPage()
  await screen.findAllByTestId('audit-entry')
  fireEvent.change(screen.getByLabelText('Person or student'), { target: { value: 'Saurabh' } })
  await waitFor(() => expect(lastAuditUrl()).toContain('q=Saurabh'))
  fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'refunds' } })
  await waitFor(() => expect(lastAuditUrl()).toContain('category=refunds'))
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-11-01' } })
  fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-11-13' } })
  await waitFor(() => expect(lastAuditUrl()).toContain('start=2026-11-01&end=2026-11-13'))
  const who = screen.getByLabelText('Done by')
  expect(within(who).getAllByRole('option').map((o) => o.textContent)).toEqual(['Anyone', 'Kavita Deshpande', 'Any student', 'System (done automatically)'])
  fireEvent.change(who, { target: { value: 'system' } })
  await waitFor(() => expect(lastAuditUrl()).toContain('actor=system'))
  fireEvent.click(screen.getByText('Advanced'))
  fireEvent.change(screen.getByLabelText('Action code'), { target: { value: 'refund.paid' } })
  await waitFor(() => expect(lastAuditUrl()).toContain('action=refund.paid'))
  fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
  await waitFor(() => expect(lastAuditUrl()).toMatch(/audit-logs\?limit=50$/))
})
