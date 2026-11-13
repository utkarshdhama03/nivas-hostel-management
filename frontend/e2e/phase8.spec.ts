// Phase 8 verification (docs/06 Phase 8): one complaint from submission to resolution and
// reopen; one week's menu copied to another hostel (inventory went with D85).
// Set-up (hostels, people) goes through the API; the steps above go through the screens.
import { type APIRequestContext, type Browser, expect, type Page, test } from '@playwright/test'

import { bearer, emailedActivationToken, pngBuffer, registerOrg, unique } from './helpers'

async function post(request: APIRequestContext, token: string, path: string, data: object) {
  const r = await request.post(`/api/v1${path}`, { headers: bearer(token), data })
  if (!r.ok()) throw new Error(`${path}: ${r.status()} ${await r.text()}`)
  return r.json()
}

async function activate(request: APIRequestContext, token: string, slug: string, userId: string, password: string, email: string) {
  const secret = await emailedActivationToken(request, token, userId, email) // D80: by email only
  const r = await request.post(`/api/v1/o/${slug}/auth/activate`, { data: { token: secret, password } })
  if (!r.ok()) throw new Error(`activate: ${r.status()} ${await r.text()}`)
}

async function signIn(browser: Browser, slug: string, portal: 'Organization' | 'Student', identifier: string, password: string): Promise<Page> {
  const page = await (await browser.newContext()).newPage()
  await page.goto(`/o/${slug}/login`)
  await page.getByRole('tab', { name: portal }).click()
  await page.getByLabel(portal === 'Organization' ? 'Employee ID or email' : 'Student ID or email').fill(identifier)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  return page
}

test('Phase 8: complaint to resolution and reopen; menu copied', async ({ browser, request }) => {
  test.setTimeout(150_000)
  const org = await registerOrg(request, 'p8')
  const t = org.token
  const session = await post(request, t, '/admin/academic-sessions', { name: '2026-27', starts_on: '2026-06-01', ends_on: '2027-05-31' })
  await post(request, t, `/admin/academic-sessions/${session.id}/make-current`, {})
  const h1 = await post(request, t, '/admin/hostels', { code: 'H1', name: 'Sunrise Boys', resident_type: 'boys' })
  const h2 = await post(request, t, '/admin/hostels', { code: 'H2', name: 'Lotus Boys', resident_type: 'boys' })
  await post(request, t, '/admin/complaint-categories', { name: 'Plumbing' })
  const photo = await request.post('/api/v1/files', { headers: bearer(t), multipart: { category: 'student_photo', file: { name: 'p.png', mimeType: 'image/png', buffer: pngBuffer() } } })
  const studentEmail = `ravi.${unique()}@example.com`
  const student = await post(request, t, '/admin/students', { full_name: 'Ravi Kumar', email: studentEmail, hostel_id: h1.id, gender: 'male', date_of_birth: '2007-04-01', admission_date: '2026-06-15', photo_file_id: (await photo.json()).file_id, send_activation_email: false })
  await activate(request, t, org.slug, student.user_id, 'amber field compass 72', studentEmail)

  // 1. The student raises a complaint with a photo.
  const st = await signIn(browser, org.slug, 'Student', student.student_code, 'amber field compass 72')
  await st.getByRole('link', { name: 'My complaints', exact: true }).click()
  await st.getByLabel('Title').fill('Leaking tap')
  await st.getByLabel("What's wrong?").fill('The bathroom tap drips all night')
  await st.getByLabel('Priority').selectOption('high')
  await st.getByLabel('Photos (up to 3, optional)').setInputFiles({ name: 'tap.png', mimeType: 'image/png', buffer: pngBuffer() })
  await st.getByRole('button', { name: 'Submit' }).click()
  await expect(st.getByTestId('my-complaints')).toContainText('Leaking tap · open')

  // 2. The Administrator (the only one, D76) takes it, starts work and resolves it with a note.
  const sa = await signIn(browser, org.slug, 'Organization', org.email, org.password)
  const wd = sa
  await wd.getByRole('link', { name: 'Complaints', exact: true }).click()
  await wd.getByRole('cell', { name: 'Leaking tap' }).click()
  const detail = wd.getByTestId('complaint-detail')
  await expect(detail.getByRole('img', { name: 'Complaint photo' })).toBeVisible()
  await detail.getByRole('button', { name: 'Assign to me' }).click()
  await detail.getByRole('button', { name: 'Start work' }).click()
  await expect(detail).toContainText('in progress')
  await detail.getByLabel('Note or comment').fill('Washer replaced')
  await detail.getByRole('button', { name: 'Resolve' }).click()
  await expect(detail).toContainText('resolved')

  // 3. It still drips: the student reopens it within the window.
  await st.reload()
  await expect(st.getByTestId('my-complaints')).toContainText('Resolution: Washer replaced')
  await st.getByLabel('Why reopen?').fill('Still dripping at night')
  await st.getByRole('button', { name: 'Reopen' }).click()
  await expect(st.getByTestId('my-complaints')).toContainText('Leaking tap · open')
  await wd.reload() // the queue was loaded before the reopen
  await wd.getByRole('cell', { name: 'Leaking tap' }).click()
  await expect(wd.getByTestId('complaint-detail')).toContainText('reopened 1×')
  await expect(wd.getByTestId('complaint-detail')).toContainText('Still dripping at night')

  // 4. The Administrator plans this week's menu for H1 and copies it to H2.
  await sa.getByRole('link', { name: 'Mess menu', exact: true }).click()
  await sa.getByLabel('Hostel', { exact: true }).selectOption({ label: 'H1 · Sunrise Boys' })
  const cells = sa.getByTestId('menu-grid').getByRole('textbox')
  await cells.nth(1).fill('Rice, dal, curd')
  await cells.nth(3).fill('Chapati, paneer')
  // Wait for the server's answer: the button is disabled DURING the save too, so "disabled" alone
  // doesn't mean saved (the copy below once ran before the save had committed: "Copied 0").
  const saved = sa.waitForResponse((r) => r.url().includes('/mess-menu/entries') && r.request().method() === 'POST')
  await sa.getByRole('button', { name: 'Save menu' }).click()
  expect((await saved).ok()).toBeTruthy()
  await expect(sa.getByRole('button', { name: 'Save menu' })).toBeDisabled() // saved: no unsaved edits left
  await expect(cells.nth(1)).toHaveValue('Rice, dal, curd')
  await sa.getByLabel('Hostel', { exact: true }).selectOption({ label: 'H2 · Lotus Boys' })
  await sa.getByLabel('From hostel').selectOption({ label: 'H1 · Sunrise Boys' })
  await sa.getByRole('button', { name: 'Copy' }).click()
  await expect(sa.getByTestId('copy-result')).toHaveText('Copied 2, kept 0')
  await expect(sa.getByTestId('menu-grid').getByRole('textbox').nth(3)).toHaveValue('Chapati, paneer')
  expect(h2.id).toBeTruthy()
})
