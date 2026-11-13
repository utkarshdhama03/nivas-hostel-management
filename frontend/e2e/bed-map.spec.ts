// The bed map (12 November): Allocations opens on it. Pick a floor, allocate a vacant bed to a student
// from the side panel, see the tile turn occupied with the student's name, check out, put a bed under
// maintenance and back; the List view searches. The keyboard alone opens and closes the panel.
import { expect, test } from '@playwright/test'

import { bearer, pngBuffer, registerOrg } from './helpers'

test('bed map: allocate from the panel, check out, maintenance, list search, keyboard', async ({ page, request }) => {
  test.setTimeout(120_000)
  const org = await registerOrg(request, 'bedmap')
  const auth = { headers: bearer(org.token) }
  const post = async (url: string, data: object) => {
    const r = await request.post(`/api/v1${url}`, { ...auth, data })
    expect(r.ok(), await r.text()).toBeTruthy()
    return r.json()
  }
  const session = await post('/admin/academic-sessions', { name: '2026-27', starts_on: '2026-06-01', ends_on: '2027-05-31' })
  await post(`/admin/academic-sessions/${session.id}/make-current`, {})
  const hostel = await post('/admin/hostels', { code: 'H1', name: 'Sunrise Boys', resident_type: 'boys' })
  await post(`/admin/hostels/${hostel.id}/room-types`, { name: 'Double', beds_per_room: 2 })
  const csv = 'building,floor,room,room_type,bed\nA,1,101,Double,A\nA,1,101,Double,B\nA,2,201,Double,A\n'
  const imported = await request.post(`/api/v1/admin/hostels/${hostel.id}/imports/rooms-beds`, { ...auth, multipart: { file: { name: 'r.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) } } })
  expect(imported.ok(), await imported.text()).toBeTruthy()
  const photo = await (await request.post('/api/v1/files', { ...auth, multipart: { category: 'student_photo', file: { name: 'p.png', mimeType: 'image/png', buffer: pngBuffer() } } })).json()
  await post('/admin/students', {
    full_name: 'Kabir Shah', email: `kabir.${org.slug}@example.com`, hostel_id: hostel.id, gender: 'male',
    date_of_birth: '2007-01-01', admission_date: '2026-06-15', photo_file_id: photo.file_id, send_activation_email: false,
  })

  await page.goto(`/o/${org.slug}/login`)
  await page.getByLabel('Employee ID or email').fill(org.email)
  await page.getByLabel('Password').fill(org.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.getByRole('link', { name: 'Allocations', exact: true }).click()

  // The map is the default view: the floor summary and the legend.
  await expect(page.getByRole('button', { name: 'Bed map', pressed: true })).toBeVisible()
  await expect(page.getByTestId('floor-summary')).toContainText('Total2')
  await expect(page.getByTestId('bed-legend')).toContainText('Maintenance')

  // Allocate bed 101/A from the side panel.
  await page.getByRole('button', { name: 'Room 101, bed A: Vacant' }).click()
  const panel = page.getByTestId('bed-panel')
  await panel.getByLabel('Find a student').fill('Kabir')
  await panel.getByLabel(/Kabir Shah/).check()
  await panel.getByRole('button', { name: 'Allocate' }).click()
  await expect(panel.getByText('Allocated.')).toBeVisible()
  const occupied = page.getByRole('button', { name: /Room 101, bed A: Occupied, Kabir Shah \(STU-\d{4}-\d{4}\) since/ })
  await expect(occupied).toBeVisible()

  // Hover shows who and since when.
  await occupied.hover()
  await expect(page.getByRole('tooltip').filter({ hasText: 'Kabir Shah' })).toBeVisible()

  // Keyboard: focus the occupied bed, Enter opens its panel, Escape closes it and focus comes back.
  await occupied.focus()
  await page.keyboard.press('Enter')
  await expect(panel.getByRole('heading', { name: 'Room 101, bed A' })).toBeFocused()
  await expect(panel.getByRole('link', { name: 'View student' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(panel).toHaveCount(0)
  await expect(occupied).toBeFocused()

  // The List view finds the stay by room number.
  await page.getByRole('button', { name: 'List' }).click()
  await page.getByLabel('Search').fill('101')
  await expect(page.getByRole('cell', { name: /Kabir Shah/ })).toBeVisible()
  await page.getByRole('button', { name: 'Bed map' }).click()

  // Check out: the bed is vacant again.
  await occupied.click()
  await panel.getByRole('button', { name: 'Check out' }).click()
  await expect(panel.getByText('Checked out.')).toBeVisible()
  await page.getByRole('button', { name: 'Close' }).click()

  // Maintenance and back, on bed 101/B.
  await page.getByRole('button', { name: 'Room 101, bed B: Vacant' }).click()
  await panel.getByRole('button', { name: 'Mark under maintenance' }).click()
  await expect(page.getByRole('button', { name: 'Room 101, bed B: Maintenance' })).toBeVisible()
  await page.getByRole('button', { name: 'Close' }).click()
  await page.getByRole('button', { name: 'Room 101, bed B: Maintenance' }).click()
  await panel.getByRole('button', { name: 'Back in use' }).click()
  await expect(page.getByRole('button', { name: 'Room 101, bed B: Vacant' })).toBeVisible()

  // Another floor.
  await page.getByRole('button', { name: 'Floor 2' }).click()
  await expect(page.getByRole('button', { name: 'Room 201, bed A: Vacant' })).toBeVisible()
})
