// Money helpers for DISPLAY. The API sends money as strings ("1250.00") and the server owns every
// calculation that matters. The few sums the screens need (overdue against not yet due) are done in
// whole paise, never in floating-point rupees: 0.1 + 0.2 is not 0.3 in a float.

export const rupees = (amount: string) => `₹${Number(amount).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`

/** "1250.50" -> 125050 (paise). */
export function paise(amount: string): number {
  const negative = amount.trim().startsWith('-')
  const [whole = '0', fraction = ''] = amount.trim().replace('-', '').split('.')
  const value = Number(whole) * 100 + Number((fraction + '00').slice(0, 2))
  return negative ? -value : value
}

/** 125050 -> "1250.50". */
export function fromPaise(value: number): string {
  const abs = Math.abs(value)
  return `${value < 0 ? '-' : ''}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, '0')}`
}

/** Dues as people read them: a negative balance is a credit, not "₹-300.00" (review Minor 7). */
export function duesWords(dues: string): { label: 'Dues' | 'Credit'; amount: string } {
  const p = paise(dues)
  return p < 0 ? { label: 'Credit', amount: rupees(fromPaise(-p)) } : { label: 'Dues', amount: rupees(dues) }
}

/** Open charges split by the organization's today: overdue (due before today) and not yet due
 *  (due today or later, or with no due date). A charge due today is not overdue yet. */
export function splitByDueDate(charges: { due_date?: string | null; outstanding: string }[], today: string): { overdue: string; upcoming: string } {
  let overdue = 0
  let upcoming = 0
  for (const c of charges) {
    if (c.due_date && c.due_date < today) overdue += paise(c.outstanding)
    else upcoming += paise(c.outstanding)
  }
  return { overdue: fromPaise(overdue), upcoming: fromPaise(upcoming) }
}
