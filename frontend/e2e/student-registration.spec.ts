// D74 (updated 11 November): a student registers on the public site, confirms the email, sees on
// Student sign-in that the request waits; the Administrator approves it on the Registration requests
// page; the student signs in. A rejected request shows the administrator's reason on sign-in.
import { type APIRequestContext, expect, test } from '@playwright/test'

import { bearer, latestEmailText, linkIn, pngBuffer, registerOrg, unique } from './helpers'

async function call(request: APIRequestContext, token: string, method: 'get' | 'post' | 'patch', path: string, data?: object) {
  const r = await request[method](`/api/v1${path}`, { headers: bearer(token), data })
  if (!r.ok()) throw new Error(`${path}: ${r.status()} ${await r.text()}`)
  return r.json()
}

test('register, confirm, wait, approve, sign in; a rejection shows its reason', async ({ browser, page, request }) => {
  test.setTimeout(120_000)
  const org = await registerOrg(request, 'reg')
  const t = org.token
  const session = await call(request, t, 'post', '/admin/academic-sessions', { name: '2026-27', starts_on: '2026-06-01', ends_on: '2027-05-31' })
  await call(request, t, 'post', `/admin/academic-sessions/${session.id}/make-current`, {})
  await call(request, t, 'post', '/admin/hostels', { code: 'H1', name: 'Aryabhata Boys Hostel', resident_type: 'boys' })
  const current = await call(request, t, 'get', '/admin/organization')
  await call(request, t, 'patch', '/admin/organization', { version: current.version, accepts_student_requests: true })

  // 1. The student, from the landing page's sidebar.
  const id = unique()
  const email = `arjun.${id}@example.com`
  const password = 'my own long passphrase'
  await page.goto('/')
  await page.getByRole('navigation', { name: 'Sign in and registration' }).getByRole('link', { name: 'Student registration' }).click()
  await expect(page).toHaveURL(/\/register\/student$/)
  await page.getByLabel('Organization sign-in name').fill(org.slug)
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByLabel('Hostel').selectOption({ label: 'Aryabhata Boys Hostel (boys)' })
  await page.getByLabel('Full name').fill('Arjun Mehta')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Date of birth').fill('2007-04-01')
  await page.getByLabel("Parent or guardian's name").fill('Rakesh Mehta')
  await page.getByLabel("Parent or guardian's phone").fill('+91 98765 22222')
  await page.getByLabel('Your photo').setInputFiles({ name: 'me.png', mimeType: 'image/png', buffer: pngBuffer() })
  await page.getByLabel('Password', { exact: true }).fill(password)
  await page.getByLabel('Password again').fill(password)
  await page.getByRole('button', { name: 'Send request' }).click()
  await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible()

  // 2. The emailed link.
  await page.goto(linkIn(await latestEmailText(email, 'Confirm your registration request')))
  await expect(page.getByText("waiting for the administrator's approval")).toBeVisible()

  // 3. Student sign-in: the right password tells the state; a wrong one tells nothing.
  const signIn = async (pw: string) => {
    await page.goto('/sign-in/student') // D80: email and password only
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill(pw)
    await page.getByRole('button', { name: 'Sign in' }).click()
  }
  await signIn('a wrong long passphrase')
  await expect(page.getByText('Wrong ID, email or password.')).toBeVisible()
  await signIn(password)
  await expect(page.getByTestId('registration-status')).toContainText("waiting for the administrator's approval")

  // 4. The Administrator approves on the Registration requests page.
  const admin = await (await browser.newContext()).newPage()
  await admin.goto(`/o/${org.slug}/login`)
  await admin.getByLabel('Employee ID or email').fill('E-0001')
  await admin.getByLabel('Password').fill(org.password)
  await admin.getByRole('button', { name: 'Sign in' }).click()
  await admin.getByRole('link', { name: 'Registration requests', exact: true }).click()
  await admin.getByRole('row', { name: /Arjun Mehta/ }).getByRole('button', { name: 'Open' }).click()
  await expect(admin.getByRole('img', { name: 'The photo the student sent' })).toBeVisible()
  await admin.getByRole('button', { name: 'Approve and admit' }).click()
  await expect(admin.getByText(/Approved\. Student ID STU-2026-/)).toBeVisible()

  // 5. The student signs in with the request's own password.
  await signIn(password)
  await expect(page.getByTestId('dashboard-title')).toHaveText('Welcome, Arjun Mehta')

  // 6. Another request, rejected: sign-in shows the reason.
  const other = `kabir.${id}@example.com`
  const form = {
    hostel_id: (await call(request, t, 'get', '/admin/hostels'))[0].id,
    full_name: 'Kabir Shah',
    email: other,
    gender: 'male',
    date_of_birth: '2007-02-02',
    guardian_name: 'Nitin Shah',
    guardian_relationship: 'father',
    guardian_phone: '+91 98765 33333',
    password,
  }
  const sent = await request.post(`/api/v1/public/organizations/${org.slug}/student-registration`, {
    multipart: { ...form, photo: { name: 'k.png', mimeType: 'image/png', buffer: pngBuffer() } },
  })
  expect(sent.status(), await sent.text()).toBe(202)
  const token = linkIn(await latestEmailText(other, 'Confirm your registration request')).split('#token=')[1]
  expect((await request.post(`/api/v1/o/${org.slug}/auth/student-registration/verify`, { data: { token } })).status()).toBe(204)
  const pending = await call(request, t, 'get', '/admin/registration-requests')
  const kabir = pending.items.find((r: { email: string }) => r.email === other)
  await call(request, t, 'post', `/admin/registration-requests/${kabir.id}/reject`, { reason: 'No rooms left for first-years' })
  await page.getByRole('button', { name: 'Sign out' }).click()
  await page.goto('/sign-in/student')
  await page.getByLabel('Email').fill(other)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByTestId('registration-status')).toContainText('rejected')
  await expect(page.getByTestId('registration-status')).toContainText('No rooms left for first-years')
})
