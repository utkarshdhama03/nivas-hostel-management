// Review Minor 5, 6 and 7: dates as DD-MM-YYYY, words instead of codes, credit and overdue wording.
import { describe, expect, it } from 'vitest'

import { showDate } from './dates'
import { label, options } from './labels'
import { duesWords, fromPaise, paise, splitByDueDate } from './money'

describe('showDate (Q48)', () => {
  it('shows a calendar date as DD-MM-YYYY', () => {
    expect(showDate('2026-11-09')).toBe('09-11-2026')
    expect(showDate('2027-04-01')).toBe('01-04-2027')
  })
  it('never guesses a date from an instant, and shows a dash for nothing', () => {
    expect(showDate('2026-11-09T18:31:00Z')).toBe('2026-11-09T18:31:00Z') // 10 November in India: not ours to cut
    expect(showDate(null)).toBe('—')
    expect(showDate(undefined)).toBe('—')
  })
})

describe('label', () => {
  it('uses plain words for stored codes', () => {
    expect(label('entryType', 'deposit_applied')).toBe('Deposit used for dues')
    expect(label('billingCycle', 'one_time')).toBe('One-time')
    expect(label('lateFee', 'per_day')).toBe('Per day')
    expect(label('refundStatus', 'requested')).toBe('Waiting for approval')
    expect(label('userStatus', 'pending_activation')).toBe('Waiting for activation')
  })
  it('never shows an unknown code raw', () => {
    expect(label('entryType', 'some_new_code')).toBe('Some new code')
    expect(label('entryType', null)).toBe('—')
  })
  it('lists the choices for a select', () => {
    expect(options('fineReason').map(([code]) => code)).toEqual(['damage', 'correction', 'proration', 'other'])
  })
})

describe('money for display', () => {
  it('converts to and from paise exactly', () => {
    expect(paise('1250.50')).toBe(125050)
    expect(paise('-300.00')).toBe(-30000)
    expect(paise('7')).toBe(700)
    expect(paise('0.1') + paise('0.2')).toBe(paise('0.3')) // the float trap
    expect(fromPaise(125050)).toBe('1250.50')
    expect(fromPaise(-5)).toBe('-0.05')
  })
  it('shows negative dues as a credit', () => {
    expect(duesWords('-300.00')).toEqual({ label: 'Credit', amount: '₹300.00' })
    expect(duesWords('1500.00')).toEqual({ label: 'Dues', amount: '₹1,500.00' })
    expect(duesWords('0.00')).toEqual({ label: 'Dues', amount: '₹0.00' })
  })
  it('splits overdue from not yet due; a charge due today is not overdue', () => {
    const charges = [
      { due_date: '2026-11-01', outstanding: '1000.10' },
      { due_date: '2026-11-09', outstanding: '0.20' },
      { due_date: '2026-11-10', outstanding: '500.00' },
      { due_date: '2026-12-10', outstanding: '250.00' },
      { due_date: null, outstanding: '50.00' },
    ]
    expect(splitByDueDate(charges, '2026-11-10')).toEqual({ overdue: '1000.30', upcoming: '800.00' })
  })
})
