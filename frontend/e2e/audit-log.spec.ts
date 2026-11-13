// The readable audit log (D89): entries are sentences under "Today" with a 12-hour time and a category
// chip; the filters narrow them; Details opens from the keyboard with the before and after; a link opens
// the student; at phone width nothing scrolls sideways.
import { expect, test } from '@playwright/test'

import { bearer, pngBuffer, registerOrg } from './helpers'

test('audit log: sentences by day, filters, details from the keyboard, link, phone width', async ({ page, request }) => {
  test.setTimeout(120_000)
  const org = await registerOrg(request, 'auditlog')
  const auth = { headers: bearer(org.token) }
  const send = async (method: 'post' | 'patch', url: string, data: object) => {
    const r = await request[method](`/api/v1${url}`, { ...auth, data })
    expect(r.ok(), await r.text()).toBeTruthy()
    return r.json()
  }
  const session = await send('post', '/admin/academic-sessions', { name: '2026-27', starts_on: '2026-06-01', ends_on: '2027-05-31' })
  await send('post', `/admin/academic-sessions/${session.id}/make-current`, {})
  const hostel = await send('post', '/admin/hostels', { code: 'H1', name: 'Sunrise Boys', resident_type: 'boys' })
  const rt = await send('post', `/admin/hostels/${hostel.id}/room-types`, { name: 'Double', beds_per_room: 2 })
  const building = await send('post', `/admin/hostels/${hostel.id}/buildings`, { name: 'A' })
  const floor = await send('post', `/admin/buildings/${building.id}/floors`, { level: 1 })
  const room = await send('post', `/admin/floors/${floor.id}/rooms`, { number: '101', room_type_id: rt.id })
  const bed = await send('post', `/admin/rooms/${room.id}/beds`, { label: 'A' })
  const photo = await (await request.post('/api/v1/files', { ...auth, multipart: { category: 'student_photo', file: { name: 'p.png', mimeType: 'image/png', buffer: pngBuffer() } } })).json()
  const student = await send('post', '/admin/students', {
    full_name: 'Kabir Shah', email: `kabir.${org.slug}@example.com`, hostel_id: hostel.id, gender: 'male',
    date_of_birth: '2007-01-01', admission_date: '2026-06-15', photo_file_id: photo.file_id, send_activation_email: false,
  })
  await send('post', '/admin/allocations', { student_id: student.id, bed_id: bed.id })
  const profile = await (await request.get('/api/v1/admin/organization', auth)).json()
  await send('patch', '/admin/organization', { version: profile.version, city: 'Pune' })

  await page.goto(`/o/${org.slug}/login`)
  await page.getByLabel('Employee ID or email').fill(org.email)
  await page.getByLabel('Password').fill(org.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.getByRole('link', { name: 'Audit log' }).click()

  // Sentences under "Today", with a 12-hour time and a category chip; the person's name, not the role.
  const today = page.getByRole('region', { name: 'Today' })
  const allocated = today.getByTestId('audit-entry').filter({ hasText: 'Asha Rao allocated bed A in room 101 to Kabir Shah' })
  await expect(allocated).toBeVisible()
  await expect(allocated).toContainText('Rooms and beds')
  await expect(allocated.locator('time')).toHaveText(/^\d{1,2}:\d{2} (AM|PM)$/)
  await expect(today).not.toContainText('super_admin')

  // Filters: the category, then a name.
  await page.getByLabel('Category').selectOption('rooms')
  await expect(page.getByTestId('audit-entry').first()).toContainText('Rooms and beds')
  await expect(page.getByTestId('audit-entry').filter({ hasNotText: 'Rooms and beds' })).toHaveCount(0)
  await page.getByLabel('Category').selectOption('')
  await page.getByLabel('Person or student').fill('Kabir')
  await expect(page.getByTestId('audit-entry').filter({ hasText: 'admitted Kabir Shah' })).toBeVisible()
  await expect(page.getByTestId('audit-entry').filter({ hasText: "changed the organization's details" })).toHaveCount(0)
  await page.getByRole('button', { name: 'Clear filters' }).click()

  // Details from the keyboard: the before and after of the edit, in words.
  const edit = page.getByTestId('audit-entry').filter({ hasText: "Asha Rao changed the organization's details" })
  await expect(edit).toContainText('City: — → Pune')
  const details = edit.getByRole('button', { name: 'Details' })
  await details.focus()
  await page.keyboard.press('Enter')
  await expect(details).toHaveAttribute('aria-expanded', 'true')
  await expect(edit.getByTestId('audit-detail').first()).toContainText(/City.*Pune/)

  // At phone width nothing scrolls sideways.
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  // The link opens the affected record.
  await allocated.getByRole('link', { name: 'Open student' }).click()
  await expect(page).toHaveURL(new RegExp(`/admin/students/${student.id}$`))
})
