// Regression tests for the UTC-date bug (frontend defaults were the UTC date, a day early in India
// from 00:00 to 05:29). The clock is frozen at the tricky moments with vi.setSystemTime.
import { afterEach, describe, expect, it, vi } from 'vitest'

import { addDays, dateIn, dayOf, firstOfMonth, mondayOf, showClock, showDay, showWhen } from './dates'

const IST = 'Asia/Kolkata'

afterEach(() => {
  vi.useRealTimers()
})

describe('dateIn: the organization date, not the UTC date', () => {
  it.each([
    // [instant (UTC), date in IST, why]
    ['2026-11-09T18:29:00Z', '2026-11-09', '23:59 IST'],
    ['2026-11-09T18:31:00Z', '2026-11-10', '00:01 IST: UTC is still the 9th'],
    ['2026-11-09T23:59:00Z', '2026-11-10', '05:29 IST, the last minute of the mismatch'],
    ['2026-03-31T18:31:00Z', '2026-04-01', '1 April 00:01 IST: a new financial year'],
    ['2026-12-31T18:31:00Z', '2027-01-01', '1 January 00:01 IST'],
    ['2027-02-28T18:31:00Z', '2027-03-01', 'month end, non-leap February'],
  ])('%s is %s in India (%s)', (instant, expected) => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(instant))
    expect(dateIn(IST)).toBe(expected)
    expect(new Date().toISOString().slice(0, 10) === expected).toBe(instant.slice(0, 10) === expected) // the old bug
  })

  it('works for any zone, not just India', () => {
    expect(dateIn('America/New_York', new Date('2026-11-10T03:00:00Z'))).toBe('2026-11-09')
    expect(dateIn('UTC', new Date('2026-11-10T03:00:00Z'))).toBe('2026-11-10')
  })
})

describe('date arithmetic', () => {
  it('crosses month, year and leap-day boundaries', () => {
    expect(addDays('2026-03-31', 1)).toBe('2026-04-01')
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01')
    expect(addDays('2028-02-28', 1)).toBe('2028-02-29')
    expect(addDays('2026-11-10', -29)).toBe('2026-10-12')
  })
  it('first of month and Monday of week', () => {
    expect(firstOfMonth('2026-04-01')).toBe('2026-04-01')
    expect(firstOfMonth('2026-11-30')).toBe('2026-11-01')
    expect(mondayOf('2026-11-15')).toBe('2026-11-09') // a Sunday
    expect(mondayOf('2026-11-09')).toBe('2026-11-09') // a Monday
    expect(mondayOf('2027-01-01')).toBe('2026-12-28') // across the year
  })
})

describe('no source file derives a calendar date from the UTC clock', () => {
  // The pattern behind the bug: the UTC date of "now" used as today. lib/dates.ts is the one place
  // allowed to format dates (and only for pure date arithmetic).
  const sources = import.meta.glob(['../**/*.{ts,tsx}', '!../**/*.test.{ts,tsx}', '!./dates.ts'], {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>

  it('scans the whole app', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(30)
  })

  it.each(Object.entries(sources))('%s', (_path, code) => {
    expect(code).not.toMatch(/new Date\(\)\.toISOString\(\)/)
    expect(code).not.toMatch(/toISOString\(\)\.slice\(0, ?(8|10)\)/)
    // Q48: instants are shown with showWhen/showDay (DD-MM-YYYY, organization time zone), never the
    // browser's default format (11/11/2026, 10:29:00 AM).
    expect(code).not.toMatch(/\.toLocale(Date|Time)?String\(\)/)
  })
})

describe('showWhen and showDay (Q48)', () => {
  it('DD-MM-YYYY, HH:MM in the organization time zone', () => {
    // 19:00 UTC on 9 November is 00:30 IST on 10 November.
    expect(showWhen('2026-11-09T19:00:00Z', IST)).toBe('10-11-2026, 00:30')
    expect(showDay('2026-11-09T19:00:00Z', IST)).toBe('10-11-2026')
    expect(showWhen('2026-11-09T19:00:00Z', 'UTC')).toBe('09-11-2026, 19:00')
    expect(showWhen(null, IST)).toBe('—')
    expect(showDay(undefined, IST)).toBe('—')
  })
})

describe('dayOf and showClock (the audit log, D89)', () => {
  it('the organization calendar day and a 12-hour time', () => {
    // 19:18 UTC on 12 November is 00:48 IST on 13 November
    expect(dayOf('2026-11-12T19:18:00Z', IST)).toBe('2026-11-13')
    expect(showClock('2026-11-12T19:18:00Z', IST)).toBe('12:48 AM')
    expect(showClock('2026-11-12T06:30:00Z', IST)).toBe('12:00 PM')
    expect(showClock('2026-11-12T08:00:00Z', IST)).toBe('1:30 PM')
    expect(showClock('2026-11-12T18:29:00Z', IST)).toBe('11:59 PM')
  })
})
