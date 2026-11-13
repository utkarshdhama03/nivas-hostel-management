// One Administrator (D75 one role, D76 one Administrator per organization): the person who registered
// the organization. The Administrator page is read-only (no way to add another); the Administrator
// approves a student's leave (no gate pass any more) and approves their own refund request, which is
// marked self-approved. The organization, hostel and student are set up through the API.
import { type APIRequestContext, type Browser, expect, type Page, test } from '@playwright/test'

import { bearer, emailedActivationToken, pngBuffer, registerOrg, unique } from './helpers'

async function post(request: APIRequestContext, token: string, path: string, data: object, headers: Record<string, string> = {}) {
  const r = await request.post(`/api/v1${path}`, { headers: { ...bearer(token), ...headers }, data })
  if (!r.ok()) throw new Error(`${path}: ${r.status()} ${await r.text()}`)
  return r.json()
}

async function newPage(browser: Browser): Promise<Page> {
  return (await browser.newContext()).newPage()
}

const indiaToday = () => new Date(Date.now() + 330 * 60_000).toISOString().slice(0, 10)

test('D76: one Administrator: no way to add another; approves leave and their own refund (self-approved)', async ({ browser, request }) => {
  test.setTimeout(150_000)
  const org = await registerOrg(request, 'admins')
  const t = org.token
  const session = await post(request, t, '/admin/academic-sessions', { name: '2026-27', starts_on: '2026-06-01', ends_on: '2027-05-31' })
  await post(request, t, `/admin/academic-sessions/${session.id}/make-current`, {})
  const hostel = await post(request, t, '/admin/hostels', { code: 'H1', name: 'Sunrise Boys', resident_type: 'boys' })
  const rt = await post(request, t, `/admin/hostels/${hostel.id}/room-types`, { name: 'Double', beds_per_room: 2 })
  const building = await post(request, t, `/admin/hostels/${hostel.id}/buildings`, { name: 'A' })
  const floor = await post(request, t, `/admin/buildings/${building.id}/floors`, { level: 1 })
  const room = await post(request, t, `/admin/floors/${floor.id}/rooms`, { number: '101', room_type_id: rt.id })
  const bed = await post(request, t, `/admin/rooms/${room.id}/beds`, { label: 'A' })
  const photo = await request.post('/api/v1/files', { headers: bearer(t), multipart: { category: 'student_photo', file: { name: 'p.png', mimeType: 'image/png', buffer: pngBuffer() } } })
  const studentEmail = `ravi.${unique()}@example.com`
  const student = await post(request, t, '/admin/students', {
    full_name: 'Ravi Kumar',
    email: studentEmail,
    hostel_id: hostel.id,
    gender: 'male',
    date_of_birth: '2007-04-01',
    admission_date: '2026-06-15',
    photo_file_id: (await photo.json()).file_id,
    send_activation_email: false,
  })
  await post(request, t, '/admin/allocations', { student_id: student.id, bed_id: bed.id })
  await post(
    request,
    t,
    '/admin/payments',
    { student_id: student.id, method: 'cash', reference: null, received_on: indiaToday(), dues_amount: '0', deposit_amount: '3000.00' },
    { 'Idempotency-Key': crypto.randomUUID() },
  )
  const activationToken = await emailedActivationToken(request, t, student.user_id, studentEmail) // D80
  await request.post(`/api/v1/o/${org.slug}/auth/activate`, { data: { token: activationToken, password: 'amber field compass 72' } })
  // The API has no route to add a second Administrator.
  expect((await request.post('/api/v1/admin/staff', { headers: bearer(t), data: { full_name: 'Second Admin' } })).status()).toBe(405)

  // 1. The Administrator signs in; the Administrator page is read-only, with the one account.
  const admin = await newPage(browser)
  await admin.goto(`/o/${org.slug}/login`)
  await admin.getByRole('tab', { name: 'Organization' }).click()
  await admin.getByLabel('Employee ID or email').fill(org.email)
  await admin.getByLabel('Password').fill(org.password)
  await admin.getByRole('button', { name: 'Sign in' }).click()
  await expect(admin.getByTestId('dashboard-title')).toBeVisible()
  for (const gone of ['Gate', 'Attendance', 'Staff', 'Administrators']) await expect(admin.getByRole('link', { name: gone, exact: true })).toHaveCount(0)
  await admin.getByRole('link', { name: 'Administrator', exact: true }).click()
  await expect(admin.getByTestId('administrator-rule')).toContainText('There is no way to add a second administrator')
  await expect(admin.getByRole('row')).toHaveCount(2) // the header and the one Administrator
  await expect(admin.getByLabel('Full name')).toHaveCount(0)
  await expect(admin.getByRole('button', { name: /Add|Deactivate|Reactivate/ })).toHaveCount(0)

  // 2. The student asks for leave; the Administrator approves it. No gate pass, no QR.
  const st = await newPage(browser)
  await st.goto(`/o/${org.slug}/login`)
  await st.getByRole('tab', { name: 'Student' }).click()
  await st.getByLabel('Student ID or email').fill(student.student_code)
  await st.getByLabel('Password').fill('amber field compass 72')
  await st.getByRole('button', { name: 'Sign in' }).click()
  await expect(st.getByRole('link', { name: 'My QR' })).toHaveCount(0)
  await st.getByRole('link', { name: 'My leave', exact: true }).click()
  const local = (offsetMin: number) =>
    st.evaluate((m) => {
      const d = new Date(Date.now() + m * 60_000)
      return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
    }, offsetMin)
  await st.getByLabel('Leaving').fill(await local(60))
  await st.getByLabel('Back by').fill(await local(24 * 60))
  await st.getByLabel('Reason').fill('Visiting family')
  await st.getByRole('button', { name: 'Send request' }).click()
  await expect(st.getByTestId('my-leave')).toContainText('pending')
  await expect(st.getByRole('columnheader', { name: 'Gate pass' })).toHaveCount(0)
  await admin.getByRole('link', { name: 'Leave', exact: true }).click()
  await admin.getByRole('row', { name: /Ravi Kumar/ }).getByRole('button', { name: 'Approve' }).click()
  await expect(admin.getByTestId('leave-queue')).not.toContainText('Ravi Kumar')
  await st.reload()
  await expect(st.getByTestId('my-leave')).toContainText('approved')

  // 3. The Administrator requests a deposit refund and approves it: marked self-approved (Q30, D76).
  await post(request, t, '/admin/refund-requests', { student_id: student.id, account: 'deposit', amount: '3000.00', reason: 'Leaving the hostel' })
  await admin.getByRole('link', { name: 'Payment queues' }).click()
  const queue = admin.getByTestId('refund-queue')
  await expect(queue).toContainText('Ravi Kumar')
  await queue.getByRole('button', { name: 'Approve' }).click()
  await expect(queue).toContainText('Approved, not paid yet')
  await expect(queue).toContainText('(self-approved)')
})
