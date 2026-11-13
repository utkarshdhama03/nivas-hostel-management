// D84: every page a notification may open is a real page of the app. The list is the database's
// (CHECK notifications_link_known, migration 0015) and the backend's (NOTIFICATION_LINKS in
// backend/app/modules/notifications/models.py); keep the three in step when a page is added.
import { matchRoutes } from 'react-router'
import { expect, test } from 'vitest'

import { routes } from '@/App'

const NOTIFICATION_LINKS = [
  '/portal/fees',
  '/portal/leave',
  '/portal/complaints',
  '/portal/announcements',
  '/admin/leave',
  '/admin/complaints',
  '/admin/registration-requests',
  '/admin/announcements',
]

function page(path: string): string | undefined {
  const matched = matchRoutes(routes, path)
  return matched?.at(-1)?.route.path
}

test('every notification link opens a real page, not the not-found page', { timeout: 20_000 }, () => {
  for (const link of NOTIFICATION_LINKS) {
    const last = page(link)
    expect(last, link).toBeDefined()
    expect(last, link).not.toBe('*')
  }
})

test('the removed pages are not real pages any more', { timeout: 20_000 }, () => {
  for (const gone of ['/portal/room', '/portal/me/room', '/portal/attendance', '/portal/gate-passes', '/portal/qr']) {
    expect(page(gone), gone).toBe('*')
  }
})
