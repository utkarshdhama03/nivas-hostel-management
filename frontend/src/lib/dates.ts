// Calendar dates in the ORGANIZATION's time zone (Q48), as 'YYYY-MM-DD'.
//
// Never derive "today" from `new Date().toISOString().slice(0, 10)`: that is the UTC date, which in
// India is still yesterday from 00:00 to 05:29. A payment recorded on 1 April at 00:30 would default
// to 31 March and get the previous financial year's receipt number. (src/lib/dates.test.ts checks
// that no source file uses that pattern.)

/** The calendar date in `timeZone` at the instant `at` (default: now). */
export function dateIn(timeZone: string, at: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(at)
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  return `${get('year')}-${get('month')}-${get('day')}`
}

/** A calendar date plus `days` (negative to go back). Pure date arithmetic, no time zone involved. */
export function addDays(isoDate: string, days: number): string {
  const d = new Date(`${isoDate}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}

/** The first day of that date's month. */
export const firstOfMonth = (isoDate: string): string => `${isoDate.slice(0, 8)}01`

/** The Monday on or before that date. */
export function mondayOf(isoDate: string): string {
  const weekday = new Date(`${isoDate}T00:00:00Z`).getUTCDay() // 0 = Sunday
  return addDays(isoDate, -((weekday + 6) % 7))
}

/** A calendar date as people here read it: DD-MM-YYYY (Q48). For DISPLAY only: date inputs and the
 *  API keep YYYY-MM-DD. It takes calendar dates, not instants: an instant has no date until a time
 *  zone is chosen, so anything else is returned unchanged rather than guessed. */
export function showDate(isoDate: string | null | undefined): string {
  if (!isoDate) return '—'
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate)
  return m ? `${m[3]}-${m[2]}-${m[1]}` : isoDate
}

function parts(instant: string, timeZone?: string): Record<string, string> {
  const fmt = new Intl.DateTimeFormat('en-GB', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
  return Object.fromEntries(fmt.formatToParts(new Date(instant)).map((p) => [p.type, p.value]))
}

/** An instant as people here read it: DD-MM-YYYY, HH:MM (24-hour), in `timeZone` (the organization's; Q48).
 *  Without a time zone (the Platform Owner, who has no organization) the browser's is used. */
export function showWhen(instant: string | null | undefined, timeZone?: string): string {
  if (!instant) return '—'
  const p = parts(instant, timeZone)
  return `${p.day}-${p.month}-${p.year}, ${p.hour}:${p.minute}`
}

/** The calendar day of an instant, DD-MM-YYYY, in `timeZone` (Q48). */
export function showDay(instant: string | null | undefined, timeZone?: string): string {
  if (!instant) return '—'
  const p = parts(instant, timeZone)
  return `${p.day}-${p.month}-${p.year}`
}

/** The organization's calendar day of an instant, 'YYYY-MM-DD' (for grouping by day; Q48). */
export function dayOf(instant: string, timeZone?: string): string {
  const p = parts(instant, timeZone)
  return `${p.year}-${p.month}-${p.day}`
}

/** The time of an instant on a 12-hour clock, '12:48 AM', in `timeZone` (the audit log, D89). */
export function showClock(instant: string, timeZone?: string): string {
  const p = parts(instant, timeZone)
  const h = Number(p.hour)
  return `${h % 12 || 12}:${p.minute} ${h < 12 ? 'AM' : 'PM'}`
}
