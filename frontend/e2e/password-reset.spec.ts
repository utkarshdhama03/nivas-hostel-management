// Forgot password by an emailed 6-digit code (D77, docs/04 §5.8), and unknown organizations.
import { expect, test } from '@playwright/test'

import { latestEmailText, linkIn, unique } from './helpers'

test('forgot password: organization and ID, emailed code, new password, sign in with it', async ({ page, request }) => {
  const id = unique()
  const slug = `reset-${id}`
  const email = `owner.${id}@example.com`
  // Set up an organization through the API (the UI path is covered by Demo 0).
  const registered = await request.post('/api/v1/public/organizations', {
    data: {
      organization: { name: 'Reset Hostels', slug, contact_email: `office.${id}@example.com` },
      super_admin: { full_name: 'Ravi Kumar', email, password: 'first long phrase 9' },
    },
  })
  expect(registered.status(), await registered.text()).toBe(202)
  const verify = await request.post(`/api/v1/o/${slug}/auth/verify-registration`, {
    data: { token: linkIn(await latestEmailText(email, 'Confirm your organization')).split('#token=')[1] },
  })
  expect(verify.status()).toBe(200)

  // From the public Organization sign-in page (D78): only the email.
  await page.goto('/sign-in/staff')
  await page.getByRole('link', { name: 'Forgot password?' }).click()
  await expect(page).toHaveURL(/\/forgot-password\/staff$/)
  await expect(page.getByLabel('Organization sign-in name')).toHaveCount(0)
  await page.getByLabel('Email').fill(email.toUpperCase())
  await page.getByRole('button', { name: 'Send code' }).click()
  await expect(page.getByText('we sent it a 6-digit code')).toBeVisible()

  const mail = await latestEmailText(email, 'Your password reset code')
  expect(mail).not.toContain('#token=') // a code, no link
  const code = mail.match(/^\s*(\d{6})\s*$/m)?.[1] ?? ''
  expect(code).toMatch(/^\d{6}$/)

  // A wrong code first: refused, and the same page stays.
  await page.getByLabel('6-digit code from the email').fill(code === '000000' ? '111111' : '000000')
  await page.getByLabel('New password').fill('second long phrase 7')
  await page.getByRole('button', { name: 'Save password' }).click()
  await expect(page.getByText(/That code didn't work/)).toBeVisible()

  await page.getByLabel('6-digit code from the email').fill(code)
  await page.getByRole('button', { name: 'Save password' }).click()
  await expect(page.getByText('All your devices were signed out')).toBeVisible()

  await page.getByRole('link', { name: 'Sign in' }).click() // back to Organization sign-in: email and password
  await expect(page).toHaveURL(/\/sign-in\/staff$/)
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill('second long phrase 7')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByTestId('dashboard-title')).toHaveText('Welcome, Ravi Kumar')

  // The old reset-link page is gone.
  await page.goto(`/o/${slug}/reset-password`)
  await expect(page.getByText('Choose a new password')).toHaveCount(0)
})

test('an unknown organization address shows a clear message', async ({ page }) => {
  await page.goto('/o/no-such-org-here/login')
  await expect(page.getByText('Organization not found')).toBeVisible()
})
