// D84: the bell opens the list; clicking a notification marks it read and opens its page; "Mark all as
// read" clears the badge; the badge shows the true unread count, "99+" above 99.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { setAccessToken } from '@/api/client'

import { badgeText, NotificationBell, PAGE_SIZE } from './NotificationBell'

type Call = { url: string; method: string }
let calls: Call[] = []
let unread = 0
const items = [
  { id: 'n1', category: 'fees', title: 'New charge', body: 'Hostel rent', link: '/portal/fees', created_at: '2026-11-12T04:00:00Z', read_at: null },
  { id: 'n2', category: 'security', title: 'Password changed', body: 'If this was not you, tell the office.', link: null, created_at: '2026-11-11T04:00:00Z', read_at: null },
  { id: 'n3', category: 'leave', title: 'Leave approved', body: 'Home leave', link: '/portal/leave', created_at: '2026-11-10T04:00:00Z', read_at: '2026-11-10T05:00:00Z' },
]

const json = (body: object) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  calls = []
  unread = 2
  setAccessToken('org', 'test-token')
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ url, method })
      if (url.endsWith('/me')) return json({ organization: { timezone: 'Asia/Kolkata', slug: 'demo-uni', name: 'Demo' }, permissions: [] })
      if (url.includes('/unread-count')) return json({ unread })
      if (url.includes('/read-all')) {
        unread = 0
        return json({ unread: 0 })
      }
      if (url.includes('/read')) {
        unread = Math.max(0, unread - 1)
        return json({ ...items[0], read_at: '2026-11-12T05:00:00Z' })
      }
      if (url.includes('/me/notifications')) return json({ items, next_cursor: null })
      return json({})
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  setAccessToken('org', null)
})

function renderAt(path = '/portal') {
  const router = createMemoryRouter(
    [
      { path: '/portal', element: <NotificationBell /> },
      { path: '/portal/fees', element: <p>The fees page</p> },
      { path: '/portal/leave', element: <p>The leave page</p> },
    ],
    { initialEntries: [path] },
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

test('the badge shows the true count, and 99+ above 99', () => {
  expect(badgeText(1)).toBe('1')
  expect(badgeText(20)).toBe('20')
  expect(badgeText(99)).toBe('99')
  expect(badgeText(100)).toBe('99+')
  expect(badgeText(1234)).toBe('99+')
})

test('a count above 99 shows 99+, and the bell says the real number', async () => {
  unread = 130
  renderAt()
  expect(await screen.findByTestId('unread-badge')).toHaveProperty('textContent', '99+')
  expect(screen.getByTestId('notification-bell').getAttribute('aria-label')).toBe('Notifications: 130 unread')
})

test('clicking the bell opens the list (one page of 20)', async () => {
  renderAt()
  const bell = await screen.findByTestId('notification-bell')
  expect(screen.queryByRole('dialog', { name: 'Notifications' })).toBeNull()
  fireEvent.click(bell)
  expect(await screen.findByRole('dialog', { name: 'Notifications' })).toBeTruthy()
  expect(await screen.findByText('New charge')).toBeTruthy()
  expect(screen.getByText('Leave approved')).toBeTruthy()
  expect(calls.some((c) => c.url.includes(`/me/notifications?limit=${PAGE_SIZE}`))).toBe(true)
  fireEvent.keyDown(document, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Notifications' })).toBeNull())
})

test('clicking a notification marks it read and opens its page', async () => {
  const router = renderAt()
  fireEvent.click(await screen.findByTestId('notification-bell'))
  fireEvent.click(await screen.findByText('New charge'))
  expect(await screen.findByText('The fees page')).toBeTruthy()
  expect(router.state.location.pathname).toBe('/portal/fees')
  expect(calls).toContainEqual({ url: '/api/v1/me/notifications/n1/read', method: 'POST' })
})

test('a notification without a link is marked read and stays on the page', async () => {
  const router = renderAt()
  fireEvent.click(await screen.findByTestId('notification-bell'))
  fireEvent.click(await screen.findByText('Password changed'))
  await waitFor(() => expect(calls).toContainEqual({ url: '/api/v1/me/notifications/n2/read', method: 'POST' }))
  expect(router.state.location.pathname).toBe('/portal')
})

test('an already-read notification opens its page without marking it again', async () => {
  renderAt()
  fireEvent.click(await screen.findByTestId('notification-bell'))
  fireEvent.click(await screen.findByText('Leave approved'))
  expect(await screen.findByText('The leave page')).toBeTruthy()
  expect(calls.some((c) => c.url.includes('/n3/read'))).toBe(false)
})

test('Mark all as read clears the badge', async () => {
  renderAt()
  expect(await screen.findByTestId('unread-badge')).toHaveProperty('textContent', '2')
  fireEvent.click(screen.getByTestId('notification-bell'))
  fireEvent.click(await screen.findByRole('button', { name: 'Mark all as read' }))
  await waitFor(() => expect(screen.queryByTestId('unread-badge')).toBeNull())
  expect(calls).toContainEqual({ url: '/api/v1/me/notifications/read-all', method: 'POST' })
})
