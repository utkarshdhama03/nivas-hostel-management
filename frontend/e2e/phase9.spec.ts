// Phase 9 verification (docs/06 Phase 9): the home overview matches a hand-calculated example
// (2 beds: 1 free, 1 occupied; D86 replaced the analytics page); the Platform Owner (created for this
// run by scripts/e2e.sh) signs in with email and password (D87), finds the organization by counts only,
// suspends it, and the Administrator's open session
// stops working at the next request; the masked audit log shows the action without values.
import { expect, test } from '@playwright/test'

import { bearer, pngBuffer, registerOrg } from './helpers'

const PO_EMAIL = process.env.HMS_E2E_PO_EMAIL ?? ''
const PO_PASSWORD = process.env.HMS_E2E_PO_PASSWORD ?? ''

test('Phase 9: home overview figures; platform suspension stops the open session; masked audit', async ({ browser, request }) => {
  test.skip(!PO_EMAIL || !PO_PASSWORD, 'run through scripts/e2e.sh, which creates the Platform Owner')
  test.setTimeout(120_000)
  const org = await registerOrg(request, 'p9')
  const t = org.token
  const post = async (path: string, data: object) => {
    const r = await request.post(`/api/v1${path}`, { headers: bearer(t), data })
    if (!r.ok()) throw new Error(`${path}: ${r.status()} ${await r.text()}`)
    return r.json()
  }
  const session = await post('/admin/academic-sessions', { name: '2026-27', starts_on: '2026-06-01', ends_on: '2027-05-31' })
  await post(`/admin/academic-sessions/${session.id}/make-current`, {})
  const hostel = await post('/admin/hostels', { code: 'H1', name: 'Sunrise Boys', resident_type: 'boys' })
  const rt = await post(`/admin/hostels/${hostel.id}/room-types`, { name: 'Double', beds_per_room: 2 })
  const building = await post(`/admin/hostels/${hostel.id}/buildings`, { name: 'A' })
  const floor = await post(`/admin/buildings/${building.id}/floors`, { level: 1 })
  const room = await post(`/admin/floors/${floor.id}/rooms`, { number: '101', room_type_id: rt.id })
  const bed = await post(`/admin/rooms/${room.id}/beds`, { label: 'A' })
  await post(`/admin/rooms/${room.id}/beds`, { label: 'B' })
  const photo = await request.post('/api/v1/files', { headers: bearer(t), multipart: { category: 'student_photo', file: { name: 'p.png', mimeType: 'image/png', buffer: pngBuffer() } } })
  const student = await post('/admin/students', { full_name: 'Ravi Kumar', email: `ravi.${crypto.randomUUID().slice(0, 8)}@example.com`, hostel_id: hostel.id, gender: 'male', date_of_birth: '2007-04-01', admission_date: '2026-06-15', photo_file_id: (await photo.json()).file_id, send_activation_email: false })
  await post('/admin/allocations', { student_id: student.id, bed_id: bed.id })

  // 1. The Administrator's dashboard: 1 of 2 beds occupied = 50%.
  const sa = await (await browser.newContext()).newPage()
  await sa.goto(`/o/${org.slug}/login`)
  await sa.getByRole('tab', { name: 'Organization' }).click()
  await sa.getByLabel('Employee ID or email').fill(org.email)
  await sa.getByLabel('Password').fill(org.password)
  await sa.getByRole('button', { name: 'Sign in' }).click()
  await expect(sa.getByTestId('overview-beds')).toContainText('1 free · 1 occupied')
  await expect(sa.getByTestId('overview-students')).toContainText('1')
  await expect(sa.getByRole('link', { name: 'Analytics' })).toHaveCount(0)

  // 2. The Platform Owner signs in with the email and the password (D87: no authenticator).
  const po = await (await browser.newContext()).newPage()
  await po.goto('/platform/login')
  await po.getByLabel('Email').fill(PO_EMAIL)
  await po.getByLabel('Password').fill(PO_PASSWORD)
  await po.getByRole('button', { name: 'Sign in' }).click()
  await expect(po.getByRole('link', { name: 'Analytics' })).toHaveCount(0) // D86: no growth charts

  // 3. Counts only, then suspension with a reason.
  await po.getByLabel('Search').fill(org.slug)
  await po.getByRole('cell', { name: new RegExp(org.slug) }).click()
  const detail = po.getByTestId('platform-org')
  await expect(detail).toContainText('1 hostels · 2 beds · 1 active students')
  await expect(po.locator('body')).not.toContainText('Ravi Kumar')
  await detail.getByLabel('Reason').fill('Unpaid subscription, per contract')
  await detail.getByRole('button', { name: 'Suspend' }).click()
  await expect(detail).toContainText('Suspended: Unpaid subscription, per contract')

  // 4. The Administrator's open session stops at the next request.
  await sa.getByRole('link', { name: 'Students', exact: true }).click()
  await expect(sa.getByText('This organization is suspended').first()).toBeVisible()

  // 5. The masked audit log shows the action, not the values.
  await po.getByRole('link', { name: 'Audit log' }).click()
  await po.getByLabel('Action').fill('organization.suspended')
  const log = po.getByTestId('platform-audit')
  await expect(log).toContainText('organization.suspended')
  await expect(log).not.toContainText('Unpaid subscription')

  // Put it back so the organization can be inspected after the run.
  await po.getByRole('link', { name: 'Organizations' }).click()
  await po.getByLabel('Search').fill(org.slug)
  await po.getByRole('cell', { name: new RegExp(org.slug) }).click()
  await po.getByTestId('platform-org').getByLabel('Reason').fill('Subscription paid in full')
  await po.getByTestId('platform-org').getByRole('button', { name: 'Reactivate' }).click()
  await expect(po.getByTestId('platform-org')).toContainText('active')
})
