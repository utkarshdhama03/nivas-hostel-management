// The code review's UI findings, done through the screens only, as on stage:
//   Critical 5 (since D75): the hostel page keeps only the leave setting; no roll-call or gate fields.
//   Critical 8: Demo 2's steps: add a term, reverse a ledger entry, apply the deposit, request a refund.
//   Should fix 7: the payment queue says whose refund it is.  Should fix 8: waiving asks for amount and reason.
import { type APIRequestContext, expect, type Page, test } from '@playwright/test'

import { bearer, pngBuffer, registerOrg } from './helpers'

async function post(request: APIRequestContext, token: string, path: string, data: object) {
  const r = await request.post(`/api/v1${path}`, { headers: bearer(token), data })
  if (!r.ok()) throw new Error(`${path}: ${r.status()} ${await r.text()}`)
  return r.json()
}

async function balanceShows(page: Page, text: string) {
  await expect(page.getByTestId('balance')).toContainText(text)
}

test('Review fixes: hostel settings, terms, waive dialog, reversal, deposit, refund queue', async ({ browser, request }) => {
  test.setTimeout(180_000)
  const org = await registerOrg(request, 'review')
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
  const student = await post(request, t, '/admin/students', { full_name: 'Ravi Kumar', email: `ravi.${crypto.randomUUID().slice(0, 8)}@example.com`, hostel_id: hostel.id, gender: 'male', date_of_birth: '2007-04-01', admission_date: '2026-06-15', photo_file_id: (await photo.json()).file_id, send_activation_email: false })
  await post(request, t, '/admin/allocations', { student_id: student.id, bed_id: bed.id })

  const sa = await (await browser.newContext()).newPage()
  await sa.goto(`/o/${org.slug}/login`)
  await sa.getByRole('tab', { name: 'Organization' }).click()
  await sa.getByLabel('Employee ID or email').fill(org.email)
  await sa.getByLabel('Password').fill(org.password)
  await sa.getByRole('button', { name: 'Sign in' }).click()
  await expect(sa.getByRole('button', { name: 'Sign out', exact: true })).toBeVisible()

  // Critical 5, since D75: the hostel page has the leave setting only (roll call and gate are gone).
  await sa.goto(`/admin/hostels/${hostel.id}`)
  const settings = sa.getByTestId('hostel-settings')
  await expect(settings.getByLabel('Roll call opens at')).toHaveCount(0)
  await settings.getByRole('checkbox', { name: /Local outings need an administrator/ }).check()
  await settings.getByRole('button', { name: 'Save hostel settings' }).click()
  await expect(settings).toContainText('Saved')

  // Critical 8: a term, added on the Organization page.
  await sa.goto('/admin/organization')
  await sa.getByLabel('Term name').fill('Term 1')
  await sa.getByLabel('Term starts').fill('2026-06-01')
  await sa.getByLabel('Term ends').fill('2026-11-30')
  await sa.getByRole('button', { name: 'Add term' }).click()
  await expect(sa.getByRole('row', { name: /2026-27/ })).toContainText('Term 1')

  // Should fix 8: waiving asks for the amount and a reason.
  await sa.goto(`/admin/students/${student.id}`)
  await sa.getByLabel('Description', { exact: true }).fill('Hostel rent')
  await sa.getByLabel('Amount ₹', { exact: true }).fill('1000')
  await sa.getByRole('button', { name: 'Add', exact: true }).click()
  await balanceShows(sa, '1,000.00')
  await sa.getByRole('row', { name: /Hostel rent/ }).getByRole('button', { name: 'Waive' }).click()
  await sa.getByLabel('Waiver amount ₹').fill('300')
  await sa.getByLabel('Waiver reason').selectOption('scholarship')
  await sa.getByLabel('Waiver note').fill('Merit scholarship, office order 12')
  await sa.getByRole('button', { name: 'Confirm waiver' }).click()
  await balanceShows(sa, '700.00')

  // Critical 8: reverse the charge from the ledger; its waiver goes with it (review Critical 1).
  await sa.getByTestId('ledger').getByRole('listitem').filter({ hasText: 'Hostel rent' }).filter({ hasText: 'charge' }).getByRole('button', { name: 'Reverse' }).click()
  await sa.getByLabel('Reason for the reversal').fill('Charged to the wrong student')
  await sa.getByRole('button', { name: 'Confirm reversal' }).click()
  await balanceShows(sa, 'Dues: ₹0.00')

  // Critical 8: take a deposit, charge a fine, apply the deposit to it.
  await sa.getByLabel('Deposit ₹').fill('2000')
  await sa.getByRole('button', { name: 'Record payment' }).click()
  await balanceShows(sa, 'Deposit held: ₹2,000.00')
  // Review Minor 9: the Administrator chooses what the fine is for (it used to be always "damage").
  await sa.getByLabel('Type').selectOption('fine')
  await sa.getByLabel('Description', { exact: true }).fill('Broken chair')
  await sa.getByLabel('Amount ₹', { exact: true }).fill('500')
  await sa.getByLabel('Fine for').selectOption('other')
  await sa.getByLabel('Fine note').fill('Chair in the common room')
  await sa.getByRole('button', { name: 'Add', exact: true }).click()
  // Review Minor 5 and 6: the ledger uses words and DD-MM-YYYY dates, not codes and ISO dates.
  const fineRow = sa.getByTestId('ledger').getByRole('listitem').filter({ hasText: 'Broken chair' })
  await expect(fineRow).toContainText(/\d{2}-\d{2}-\d{4} · Dues · Fine · ₹500\.00/)
  await sa.getByLabel('Amount to apply ₹').fill('500')
  await sa.getByRole('button', { name: 'Apply deposit' }).click()
  await balanceShows(sa, 'Deposit held: ₹1,500.00')

  // Critical 8 + Should fix 7: request a deposit refund; the queue says whose it is.
  await sa.getByLabel('Refund from').selectOption('deposit')
  await sa.getByLabel('Refund amount ₹').fill('1500')
  await sa.getByLabel('Refund reason').fill('Leaving the hostel')
  await sa.getByRole('button', { name: 'Request refund' }).click()
  await sa.goto('/admin/payment-queues')
  await expect(sa.getByTestId('refund-queue')).toContainText(`Ravi Kumar (${student.student_code})`)
  await expect(sa.getByTestId('refund-queue')).toContainText('From the deposit')
  await expect(sa.getByTestId('refund-queue')).toContainText('Waiting for approval')
  // Review Minor 10: rejecting asks for the reason in a form on the page, not in a browser prompt.
  await sa.getByTestId('refund-queue').getByRole('button', { name: 'Reject' }).click()
  await expect(sa.getByLabel('Why is it rejected?')).toBeVisible()
  await sa.getByTestId('refund-queue').getByRole('button', { name: 'Cancel' }).click()
  // Review Minor 12: a waiting request can be withdrawn; the same money can then be requested again.
  await sa.getByTestId('refund-queue').getByRole('button', { name: 'Withdraw' }).click()
  await expect(sa.getByTestId('refund-queue')).toContainText('Withdrawn')

  // Q93: exit with the deposit still held warns with the amounts, then records the exit with a reason.
  await sa.goto(`/admin/students/${student.id}`)
  await sa.getByRole('button', { name: 'Check out' }).click()
  await expect(sa.getByRole('button', { name: 'Check out' })).toHaveCount(0) // done before the exit (it needs the bed freed)
  await sa.getByLabel('Exit date').fill(new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date()))
  await sa.getByLabel('Reason', { exact: true }).selectOption('withdrawn')
  await sa.getByRole('button', { name: 'Record exit' }).click()
  await expect(sa.getByTestId('exit-warning')).toContainText('Deposit held ₹1,500.00')
  await sa.getByLabel('Why record the exit anyway?').fill('Deposit refund to be requested again')
  await sa.getByRole('button', { name: 'Record exit anyway' }).click()
  await expect(sa.getByText('exited', { exact: false }).first()).toBeVisible()
})
