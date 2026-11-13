// Demo 1, the minimal working demo (docs/06 §3 Phase 4): organization -> hostel (partly by CSV)
// -> (the one Administrator, D76) student admission with photo and masked Aadhaar PDF -> student activation
// through a copied link -> bed allocation (bed board updates) -> the student signs in (D79: no room view).
// Finally: another organization gets 404, and a double booking is refused.
import { expect, type Page, test } from '@playwright/test'

import { bearer, latestEmailText, linkIn, pdfBuffer, pngBuffer, registerOrg } from './helpers'

async function signIn(page: Page, slug: string, portal: 'Organization' | 'Student', identifier: string, password: string) {
  await page.goto(`/o/${slug}/login`)
  await page.getByRole('tab', { name: portal }).click()
  await page.getByLabel(portal === 'Organization' ? 'Employee ID or email' : 'Student ID or email').fill(identifier)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

async function activate(page: Page, link: string, password: string) {
  await page.goto(link) // the path and #token from the email (D80)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Activate' }).click()
  await expect(page.getByText('Account activated')).toBeVisible()
}

test('Demo 1: hostel, admission, activation, allocation, portal; isolation and no double booking', async ({ page, request }) => {
  test.setTimeout(120_000)
  const org = await registerOrg(request, 'demo1')
  // The academic session is set up through the API (its screen is covered elsewhere).
  const session = await (await request.post('/api/v1/admin/academic-sessions', { headers: bearer(org.token), data: { name: '2026-27', starts_on: '2026-06-01', ends_on: '2027-05-31' } })).json()
  await request.post(`/api/v1/admin/academic-sessions/${session.id}/make-current`, { headers: bearer(org.token) })

  // 1. The Administrator signs in (password + TOTP) and sets up a hostel.
  await signIn(page, org.slug, 'Organization', org.email, org.password)
  await page.getByRole('link', { name: 'Hostels', exact: true }).click()
  await page.getByLabel('Code').fill('H1')
  await page.getByLabel('Name', { exact: true }).fill('Sunrise Boys')
  await page.getByRole('button', { name: 'Create' }).click()
  await page.getByRole('link', { name: 'Sunrise Boys' }).click()
  const roomTypeForm = page.locator('form', { hasText: 'Add room type' })
  await roomTypeForm.getByLabel('Name').fill('Double')
  await roomTypeForm.getByLabel('Beds per room').fill('2')
  await roomTypeForm.getByRole('button', { name: 'Add room type' }).click()
  await expect(page.getByText('Double: 2 bed(s)')).toBeVisible()
  // 2. Rooms and beds by CSV.
  await page.getByLabel('CSV file').setInputFiles({
    name: 'rooms.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('building,floor,room,room_type,bed\nA,1,101,Double,A\nA,1,101,Double,B\nA,1,102,Double,A\nA,1,102,Double,B\n'),
  })
  await page.getByRole('button', { name: 'Import' }).click()
  await expect(page.getByText('Imported 4 rows')).toBeVisible()
  await expect(page.getByTestId('bed-counts')).toHaveText('4 vacant')

  // 3. The Administrator (the only one, D76) admits a student with a photo and a masked Aadhaar PDF.
  await page.getByRole('link', { name: 'Students', exact: true }).click()
  await page.getByRole('link', { name: 'Admit student' }).click()
  await page.getByLabel('Full name').fill('Ravi Kumar')
  const studentEmail = `ravi.${org.slug}@example.com`
  await page.getByLabel(/^Email/).fill(studentEmail)
  await page.locator('#a-hostel').selectOption({ index: 1 })
  await page.getByLabel('Date of birth').fill('2007-04-01')
  await page.locator('#a-photo').setInputFiles({ name: 'ravi.png', mimeType: 'image/png', buffer: pngBuffer() })
  await page.getByRole('button', { name: 'Admit' }).click()
  await expect(page.getByTestId('student-name')).toHaveText('Ravi Kumar')
  const studentUrl = page.url()
  const studentId = studentUrl.split('/').pop()!
  const studentCode = (await page.getByText(/STU-\d{4}-\d{4}/).first().textContent())!.match(/STU-\d{4}-\d{4}/)![0]
  await page.locator('#d-file').setInputFiles({ name: 'aadhaar.pdf', mimeType: 'application/pdf', buffer: pdfBuffer() })
  await page.locator('#d-masked').fill('XXXXXXXX1234')
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Upload document' }).click()
  await expect(page.getByText('aadhaar XXXXXXXX1234')).toBeVisible()

  // 4. The activation link comes by email (D80: no copied links), sent at admission. The student's page
  // offers to send it again (a resend replaces the link: tests/test_org_setup.py covers that).
  await expect(page.getByRole('button', { name: 'Send the activation email again' })).toBeVisible()
  const studentLink = linkIn(await latestEmailText(studentEmail, 'Activate your account'))

  // 5. Allocate a bed; the bed board updates.
  await page.locator('#al-bed').selectOption({ index: 1 })
  await page.getByRole('button', { name: 'Allocate' }).click()
  await expect(page.getByTestId('current-allocation')).toContainText('Staying since')
  await page.getByRole('link', { name: 'Hostels', exact: true }).click()
  await page.getByRole('link', { name: 'Sunrise Boys' }).click()
  await expect(page.getByTestId('bed-counts')).toHaveText(/1 occupied/)
  await page.getByRole('button', { name: 'Sign out' }).click()

  // 6. The student activates and signs in. D79: no "My room" page any more.
  await activate(page, studentLink, 'amber field compass 72')
  await signIn(page, org.slug, 'Student', studentCode, 'amber field compass 72')
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'My room' })).toHaveCount(0)

  // 7. Another organization can't see any of it, and a double booking is refused.
  const other = await registerOrg(request, 'demo1b')
  const foreign = await request.get(`/api/v1/admin/students/${studentId}`, { headers: bearer(other.token) })
  expect(foreign.status()).toBe(404)
  const board = await (await request.get('/api/v1/admin/hostels', { headers: bearer(org.token) })).json()
  const beds = await (await request.get(`/api/v1/admin/hostels/${board[0].id}/bed-board`, { headers: bearer(org.token) })).json()
  const occupied = beds.rooms.flatMap((r: { beds: { id: string; state: string }[] }) => r.beds).find((b: { state: string }) => b.state === 'occupied')
  const photo = await request.post('/api/v1/files', { headers: bearer(org.token), multipart: { category: 'student_photo', file: { name: 'p.png', mimeType: 'image/png', buffer: pngBuffer() } } })
  const second = await (
    await request.post('/api/v1/admin/students', {
      headers: bearer(org.token),
      data: { full_name: 'Second Student', email: `second.${org.slug}@example.com`, hostel_id: board[0].id, gender: 'male', date_of_birth: '2007-01-01', admission_date: '2026-06-15', photo_file_id: (await photo.json()).file_id, send_activation_email: false },
    })
  ).json()
  const double = await request.post('/api/v1/admin/allocations', { headers: bearer(org.token), data: { student_id: second.id, bed_id: occupied.id } })
  expect(double.status()).toBe(409)
  expect((await double.json()).code).toBe('bed_already_allocated')
})
