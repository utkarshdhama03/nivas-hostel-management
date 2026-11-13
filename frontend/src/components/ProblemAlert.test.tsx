// Review Should fix 9: a rate-limited answer says how long to wait (the server sends Retry-After),
// instead of a vague "wait a few minutes", so a presenter or a user knows what to do.
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { ApiError, type Problem } from '@/api/client'

import { ProblemAlert } from './ProblemAlert'

const limited = (code: string, retryAfter?: number): Problem => ({
  type: `/problems/${code}`,
  title: 'Too many attempts. Try again later.',
  status: 429,
  code,
  errors: [],
  ...(retryAfter === undefined ? {} : { retry_after_seconds: retryAfter }),
})

afterEach(cleanup)

describe('rate-limited answers', () => {
  it.each([
    ['too_many_attempts', 300, 'Try again in 5 minutes.'],
    ['rate_limited', 3600, 'Try again in 60 minutes.'],
    ['too_many_attempts', 20, 'Try again in 1 minute.'],
  ])('%s with Retry-After %i says "%s"', (code, seconds, text) => {
    render(<ProblemAlert error={new ApiError(limited(code, seconds))} />)
    expect(screen.getByText(text, { exact: false })).toBeDefined()
  })

  it('without Retry-After keeps a general message', () => {
    render(<ProblemAlert error={new ApiError(limited('too_many_attempts'))} />)
    expect(screen.getByText('Too many attempts', { exact: false })).toBeDefined()
  })
})
