/// <reference lib="dom" />
// (page.evaluate callbacks run in the browser, so this file needs the DOM types.)
// NFR-13 final pass (docs/06 Phase 10): keyboard navigation, visible focus and accessible names.
// Colour contrast is checked on the design tokens by src/theme-contrast.test.ts.
//
// A home-grown check rather than axe-core: axe-core is MPL-2.0, outside the pre-approved licences
// (docs/07), and the rules we committed to are few enough to check directly:
//   * sign-in works with the keyboard alone (Tab, typing, Enter);
//   * every element reached with Tab shows a focus indicator (outline or ring);
//   * every visible form control has a label; every button, link and tab has a name; every image has alt;
//   * <html lang> is set and each signed-in page has exactly one <main>.
import { type APIRequestContext, expect, type Page, test } from '@playwright/test'

import { bearer, emailedActivationToken, pngBuffer, registerOrg, unique } from './helpers'

async function post(request: APIRequestContext, token: string, path: string, data: object) {
  const r = await request.post(`/api/v1${path}`, { headers: bearer(token), data })
  if (!r.ok()) throw new Error(`${path}: ${r.status()} ${await r.text()}`)
  return r.json()
}

/** Press Tab until the focused element has this id (keyboard only; fails after 25 presses). */
async function tabTo(page: Page, id: string) {
  for (let i = 0; i < 25; i++) {
    if ((await page.evaluate(() => document.activeElement?.id)) === id) return
    await page.keyboard.press('Tab')
  }
  throw new Error(`could not reach #${id} with Tab`)
}

/** Problems with names and labels on the current page (empty = fine). */
async function nameProblems(page: Page, shell: boolean): Promise<string[]> {
  return page.evaluate((shell) => {
    const shown = (el: Element) => {
      const r = el.getBoundingClientRect()
      return (r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden') || el.classList.contains('sr-only')
    }
    const byIds = (ids: string | null) => (ids ?? '').split(/\s+/).map((i) => document.getElementById(i)?.textContent ?? '').join(' ')
    const name = (el: Element) =>
      [
        el.getAttribute('aria-label'),
        byIds(el.getAttribute('aria-labelledby')),
        'labels' in el ? [...((el as HTMLInputElement).labels ?? [])].map((l) => l.textContent).join(' ') : '',
        el.getAttribute('title'),
      ]
        .join(' ')
        .trim()
    const describe = (el: Element) => el.outerHTML.slice(0, 120)
    const out: string[] = []
    for (const el of document.querySelectorAll('input:not([type=hidden]), select, textarea')) {
      if (shown(el) && !name(el)) out.push(`form control without a label: ${describe(el)}`)
    }
    for (const el of document.querySelectorAll('button, a[href], [role=button], [role=tab], [role=link]')) {
      const imgName = el.querySelector('img[alt]:not([alt=""]), svg[aria-label]')
      if (shown(el) && !name(el) && !el.textContent?.trim() && !imgName) out.push(`no accessible name: ${describe(el)}`)
    }
    for (const img of document.querySelectorAll('img:not([alt])')) out.push(`image without alt: ${describe(img)}`)
    if (!document.documentElement.lang) out.push('<html> has no lang')
    if (shell && document.querySelectorAll('main').length !== 1) out.push(`expected one <main>, found ${document.querySelectorAll('main').length}`)
    return out
  }, shell)
}

/** Tab through the page; every element that takes focus must show an outline or a ring. */
async function focusProblems(page: Page, presses = 30): Promise<string[]> {
  const out: string[] = []
  // Start from the top of the page without clicking (a click could land on a link and navigate).
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  for (let i = 0; i < presses; i++) {
    await page.keyboard.press('Tab')
    const r = await page.evaluate(() => {
      const el = document.activeElement
      if (!el || el === document.body) return null
      const s = getComputedStyle(el)
      const outline = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0
      return { ok: outline || s.boxShadow !== 'none', what: `${el.tagName.toLowerCase()} id=${el.id} type=${el.getAttribute('type')} focus=${el.matches(':focus')} focus-visible=${el.matches(':focus-visible')} outline=${s.outlineStyle}/${s.outlineWidth} shadow=${s.boxShadow}` }
    })
    if (r && !r.ok) out.push(`no visible focus: ${r.what}`)
  }
  return [...new Set(out)]
}

async function check(page: Page, url: string, shell = true) {
  await page.goto(url)
  if (shell) await expect(page.getByRole('button', { name: 'Sign out', exact: true })).toBeVisible()
  await page.waitForLoadState('networkidle')
  const problems = [...(await nameProblems(page, shell)), ...(await focusProblems(page))]
  expect(problems, `${url}\n${problems.join('\n')}`).toEqual([])
}

test('NFR-13: keyboard sign-in, visible focus and accessible names on every page', async ({ browser, request }) => {
  test.setTimeout(240_000)
  const org = await registerOrg(request, 'a11y')
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
  const student = await post(request, t, '/admin/students', { full_name: 'Ravi Kumar', email: studentEmail, hostel_id: hostel.id, gender: 'male', date_of_birth: '2007-04-01', admission_date: '2026-06-15', photo_file_id: (await photo.json()).file_id, send_activation_email: false })
  await post(request, t, '/admin/allocations', { student_id: student.id, bed_id: bed.id })
  const activationToken = await emailedActivationToken(request, t, student.user_id, studentEmail)
  const pw = 'amber field compass 72'
  const act = await request.post(`/api/v1/o/${org.slug}/auth/activate`, { data: { token: activationToken, password: pw } })
  expect(act.ok(), await act.text()).toBeTruthy()

  // Public pages.
  const anon = await (await browser.newContext()).newPage()
  const publicPages = ['/', '/sign-in/student', '/sign-in/staff', '/register/student', '/register', '/forgot-password', '/forgot-password/staff']
  for (const url of [...publicPages, `/o/${org.slug}/login`, `/o/${org.slug}/forgot-password`, '/platform/login']) await check(anon, url, false)
  // The landing page on a phone: the sidebar folds behind "Menu", and opens from the keyboard.
  const phone = await (await browser.newContext({ viewport: { width: 390, height: 844 } })).newPage()
  await check(phone, '/', false)
  const menu = phone.getByRole('button', { name: 'Menu' })
  await menu.focus()
  await phone.keyboard.press('Enter')
  await expect(menu).toHaveAttribute('aria-expanded', 'true')
  await expect(phone.getByRole('navigation', { name: 'Sign in and registration' }).getByRole('link', { name: 'Organization sign-in' })).toBeVisible()

  // 1. The Administrator signs in with the keyboard only (email and password, D87).
  const sa = await (await browser.newContext()).newPage()
  await sa.goto(`/o/${org.slug}/login`)
  await sa.locator('body').click({ position: { x: 1, y: 1 } })
  await tabTo(sa, 'identifier') // the Organization tab is the default
  await sa.keyboard.type(org.email)
  await tabTo(sa, 'password')
  await sa.keyboard.type(org.password)
  await sa.keyboard.press('Enter')
  await expect(sa.getByRole('button', { name: 'Sign out' })).toBeVisible()

  const admin = ['', 'security', 'organization', 'hostels', `hostels/${hostel.id}`, 'staff', 'students', 'students/new', `students/${student.id}`]
  admin.push('allocations', 'fee-structures', 'payment-queues', 'leave', 'audit', 'complaints', 'mess', 'announcements')
  admin.push('registration-requests')
  for (const p of admin) await check(sa, `/admin/${p}`)

  // 2. The student starts on the landing page and signs in with the keyboard only: Tab to "Student
  // sign-in" in the sidebar, Enter, then the email and the password (D80).
  const st = await (await browser.newContext()).newPage()
  await st.goto('/')
  await st.locator('body').click({ position: { x: 1, y: 1 } })
  for (let i = 0; i < 25 && (await st.evaluate(() => document.activeElement?.textContent?.trim())) !== 'Student sign-in'; i++) await st.keyboard.press('Tab')
  await st.keyboard.press('Enter')
  await expect(st).toHaveURL(/\/sign-in\/student$/)
  await tabTo(st, 'identifier')
  await st.keyboard.type(studentEmail)
  await tabTo(st, 'password')
  await st.keyboard.type(pw)
  await st.keyboard.press('Enter')
  await expect(st.getByRole('button', { name: 'Sign out' })).toBeVisible()
  for (const p of ['', 'profile', 'fees', 'leave', 'complaints', 'mess', 'announcements', 'security']) {
    await check(st, `/portal/${p}`)
  }
})
