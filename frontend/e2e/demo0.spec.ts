// Demo 0 (docs/06 Phase 1): register an organization, confirm the email in Mailpit (which signs the
// administrator in, D87: no authenticator), see the admin home; sign out and back in with only the
// email and the password; a wrong password is refused and an old refresh cookie is rejected.
import { expect, test } from '@playwright/test'

import { latestEmailText, linkIn, unique } from './helpers'

const PASSWORD = 'violet tram 4 eleven'

test('Demo 0: register, confirm, signed in; sign in again with email and password; old cookie rejected', async ({ page, request }) => {
  const id = unique()
  const slug = `demo-${id}`
  const email = `owner.${id}@example.com`

  // 1. Register. No two-step verification anywhere (D87), so no badge and no setup screens.
  await page.goto('/register')
  await expect(page.getByTestId('dev-badge')).toHaveCount(0)
  await page.getByLabel('Organization name').fill('Sunrise Hostels')
  await page.getByLabel('Organization sign-in name').fill(slug)
  await page.getByLabel('Office email').fill(`office.${id}@example.com`)
  await page.getByLabel('Your full name').fill('Asha Rao')
  await page.getByLabel('Your email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Register' }).click()
  await expect(page.getByText('Check your email')).toBeVisible()

  // 2. Confirm through the link in Mailpit: the administrator is signed in at once.
  await page.goto(linkIn(await latestEmailText(email, 'Confirm your organization')))
  await expect(page).toHaveURL(/\/admin$/)
  await expect(page.getByTestId('dashboard-title')).toHaveText('Welcome, Asha Rao')
  await expect(page.getByTestId('overview')).toBeVisible() // D86: the home overview, no charts

  // A reload loses the in-memory access token; the refresh cookie restores the session.
  await page.reload()
  await expect(page.getByTestId('dashboard-title')).toHaveText('Welcome, Asha Rao')
  const cookieBeforeRotation = (await page.context().cookies()).find((c) => c.name === 'hms_refresh')!.value

  // 3. Sign out and in again with only the email and the password; a wrong password is refused.
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page).toHaveURL(new RegExp(`/o/${slug}/login$`))
  await page.getByLabel('Employee ID or email').fill(email)
  await page.getByLabel('Password').fill('not the password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toContainText('Wrong')
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByTestId('dashboard-title')).toBeVisible()
  await expect(page.getByText('6-digit code from your authenticator app')).toHaveCount(0)

  // 4. The old refresh cookie (rotated on reload, then the session was signed out) is rejected.
  const res = await request.post('/api/v1/auth/refresh', {
    headers: { 'X-Requested-With': 'hms', Origin: 'http://localhost:5173', Cookie: `hms_refresh=${cookieBeforeRotation}` },
  })
  expect(res.status()).toBe(401)
})
