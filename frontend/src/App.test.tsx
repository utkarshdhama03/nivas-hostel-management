import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'

import { App } from './App'

test('the start page offers sign-in and registration', async () => {
  window.history.pushState({}, '', '/')
  render(<App />)
  expect(await screen.findByRole('heading', { level: 1, name: 'Hostel management for colleges and hostel operators' }, { timeout: 10_000 })).toBeDefined()
  expect(screen.getAllByRole('link', { name: 'Register your organization' }).length).toBeGreaterThan(0)
})
