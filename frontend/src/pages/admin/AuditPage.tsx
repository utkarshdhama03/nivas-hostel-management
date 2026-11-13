// The organization's audit log (docs/04 §12.17, D89): the Administrator only (Q17), newest first,
// "Load more" follows the cursor. Each entry comes from the server as a plain sentence with its category,
// a summary and the before/after details; the stored row is never changed (presentation only).
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import {
  BedDouble,
  Bot,
  CalendarDays,
  ChevronDown,
  CircleHelp,
  GraduationCap,
  IndianRupee,
  KeyRound,
  Megaphone,
  MessageSquareWarning,
  Settings,
  Undo2,
  UserPlus,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useId, useState } from 'react'
import { Link } from 'react-router'

import { api } from '@/api/client'
import type { AuditLogPage, StaffOut } from '@/api/types'
import { useOrgTimeZone, useOrgToday } from '@/auth/session'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { PageHeader, Select } from '@/components/ui/table'
import { addDays, dayOf, showClock, showDate } from '@/lib/dates'
import { cn } from '@/lib/utils'

type Entry = AuditLogPage['items'][number]

type CategoryLook = { label: string; icon: LucideIcon; chip: string }
const OTHER: CategoryLook = { label: 'Other', icon: CircleHelp, chip: 'bg-muted text-foreground' }

/** One icon, colour and name per category. The name is always shown: colour is never the only signal. */
export const CATEGORIES: Record<string, CategoryLook> = {
  payments: { label: 'Payments', icon: IndianRupee, chip: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200' },
  refunds: { label: 'Refunds', icon: Undo2, chip: 'bg-violet-100 text-violet-900 dark:bg-violet-950 dark:text-violet-200' },
  students: { label: 'Students', icon: GraduationCap, chip: 'bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200' },
  registration: { label: 'Registration requests', icon: UserPlus, chip: 'bg-indigo-100 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-200' },
  rooms: { label: 'Rooms and beds', icon: BedDouble, chip: 'bg-amber-100 text-amber-950 dark:bg-amber-950 dark:text-amber-200' },
  leave: { label: 'Leave', icon: CalendarDays, chip: 'bg-lime-100 text-lime-900 dark:bg-lime-950 dark:text-lime-200' },
  complaints: { label: 'Complaints', icon: MessageSquareWarning, chip: 'bg-rose-100 text-rose-900 dark:bg-rose-950 dark:text-rose-200' },
  notices: { label: 'Notices', icon: Megaphone, chip: 'bg-fuchsia-100 text-fuchsia-900 dark:bg-fuchsia-950 dark:text-fuchsia-200' },
  settings: { label: 'Settings', icon: Settings, chip: 'bg-zinc-200 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100' },
  signin: { label: 'Sign-in', icon: KeyRound, chip: 'bg-cyan-100 text-cyan-900 dark:bg-cyan-950 dark:text-cyan-200' },
  other: OTHER,
}

/** "Today", "Yesterday", or DD-MM-YYYY, in the organization's calendar. */
export function dayHeading(day: string, today: string): string {
  if (day === today) return 'Today'
  if (day === addDays(today, -1)) return 'Yesterday'
  return showDate(day)
}

/** Entries grouped by the organization's calendar day, keeping the newest-first order. */
export function groupByDay(items: Entry[], timeZone: string): { day: string; items: Entry[] }[] {
  const groups: { day: string; items: Entry[] }[] = []
  for (const it of items) {
    const day = dayOf(it.occurred_at, timeZone)
    const last = groups[groups.length - 1]
    if (last && last.day === day) last.items.push(it)
    else groups.push({ day, items: [it] })
  }
  return groups
}

function CategoryChip({ category }: { category: string }) {
  const c = CATEGORIES[category] ?? OTHER
  const Icon = c.icon
  return (
    <span className={cn('inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium', c.chip)}>
      <Icon size={13} aria-hidden />
      {c.label}
    </span>
  )
}

/** Before and after, one field per line: the name, then "before → after" (just the value for something
 *  new). Wraps on a phone instead of squeezing three columns. */
function Details({ entry }: { entry: Entry }) {
  return (
    <dl aria-label="What changed" className="mt-2 text-sm">
      {entry.details.map((d) => (
        <div key={d.field} className="flex flex-wrap gap-x-3 border-t border-border py-1.5" data-testid="audit-detail">
          <dt className="w-full font-medium sm:w-44 sm:shrink-0">{d.label}</dt>
          <dd className="min-w-0 flex-1">
            {d.before !== null && (
              <>
                <span className="sr-only">Before: </span>
                <span className="text-muted-foreground">{d.before}</span>
                <span aria-hidden> → </span>
                <span className="sr-only">, after: </span>
              </>
            )}
            {d.after ?? '—'}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function EntryRow({ entry, timeZone }: { entry: Entry; timeZone: string }) {
  const [open, setOpen] = useState(false)
  const detailsId = useId()
  const automatic = entry.actor_kind === 'system'
  return (
    <li className="min-w-0 rounded-lg border border-border bg-card p-3 sm:p-4 [overflow-wrap:anywhere]" data-testid="audit-entry">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <time dateTime={entry.occurred_at} className="font-medium tabular-nums text-foreground">
          {showClock(entry.occurred_at, timeZone)}
        </time>
        <CategoryChip category={entry.category} />
        <span>
          By <span className="font-medium text-foreground">{entry.actor_name}</span>
          {entry.actor_kind === 'student' && ' (student)'}
        </span>
        {automatic && (
          <span className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5" title="Done automatically by the system, not by a person">
            <Bot size={12} aria-hidden />
            Done automatically
          </span>
        )}
      </div>
      <p className="mt-1.5 text-sm sm:text-base">{entry.sentence}</p>
      {entry.summary && <p className="mt-1 text-sm text-muted-foreground">{entry.summary}</p>}
      {(entry.details.length > 0 || entry.link_to) && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {entry.details.length > 0 && (
            <Button variant="outline" size="sm" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen((v) => !v)}>
              <ChevronDown size={14} aria-hidden className={cn('transition-transform motion-reduce:transition-none', open && 'rotate-180')} />
              Details
            </Button>
          )}
          {entry.link_to && (
            <Link to={entry.link_to} className="text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:underline">
              {entry.link_label}
            </Link>
          )}
        </div>
      )}
      {open && (
        <div id={detailsId}>
          <Details entry={entry} />
        </div>
      )}
    </li>
  )
}

export function AuditPage() {
  const tz = useOrgTimeZone()
  const today = useOrgToday()
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('') // the search, applied once typing pauses
  const [category, setCategory] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [actor, setActor] = useState('')
  const [action, setAction] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim().length >= 2 ? search.trim() : ''), 300)
    return () => clearTimeout(t)
  }, [search])

  const staff = useQuery({ queryKey: ['staff'], queryFn: () => api<{ items: StaffOut[] }>('/admin/staff?page_size=100') })
  const params = new URLSearchParams({ limit: '50' })
  if (q) params.set('q', q)
  if (category) params.set('category', category)
  if (from) params.set('start', from)
  if (to) params.set('end', to)
  if (actor) params.set('actor', actor)
  if (action.trim()) params.set('action', action.trim())
  const filters = params.toString()
  const logs = useInfiniteQuery({
    queryKey: ['audit-logs', filters],
    initialPageParam: '',
    queryFn: ({ pageParam }) => api<AuditLogPage>(`/admin/audit-logs?${filters}${pageParam ? `&cursor=${pageParam}` : ''}`),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
  const rows = logs.data?.pages.flatMap((p) => p.items) ?? []
  const filtered = Boolean(q || category || from || to || actor || action.trim())
  const clear = () => {
    setSearch('')
    setQ('')
    setCategory('')
    setFrom('')
    setTo('')
    setActor('')
    setAction('')
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Audit log" />
      <p className="text-sm text-muted-foreground">Everything done in this organization, newest first. Entries can never be edited or deleted.</p>

      <form role="search" aria-label="Filter the audit log" className="grid grid-cols-1 gap-3 rounded-lg border border-border p-3 sm:grid-cols-2 lg:grid-cols-5 [&_input]:min-w-0 [&_select]:min-w-0" onSubmit={(e) => e.preventDefault()}>
        <div className="space-y-1 lg:col-span-2">
          <Label htmlFor="audit-search">Person or student</Label>
          <Input id="audit-search" type="search" placeholder="Name or Student ID" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="audit-category">Category</Label>
          <Select id="audit-category" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {Object.entries(CATEGORIES).map(([key, c]) => (
              <option key={key} value={key}>
                {c.label}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="audit-from">From</Label>
          <Input id="audit-from" type="date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="audit-to">To</Label>
          <Input id="audit-to" type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} />
        </div>
        <div className="space-y-1 lg:col-span-2">
          <Label htmlFor="audit-who">Done by</Label>
          <Select id="audit-who" value={actor} onChange={(e) => setActor(e.target.value)}>
            <option value="">Anyone</option>
            {(staff.data?.items ?? []).map((s) => (
              <option key={s.user_id} value={s.user_id}>
                {s.full_name}
              </option>
            ))}
            <option value="students">Any student</option>
            <option value="system">System (done automatically)</option>
          </Select>
        </div>
        <details className="sm:col-span-2 lg:col-span-2 [&[open]>summary]:mb-2">
          <summary className="cursor-pointer text-sm font-medium">Advanced</summary>
          <Label htmlFor="audit-action">Action code</Label>
          <Input id="audit-action" placeholder="e.g. refund.paid" value={action} onChange={(e) => setAction(e.target.value)} />
        </details>
        <div className="flex items-end">
          {filtered && (
            <Button type="button" variant="ghost" onClick={clear}>
              Clear filters
            </Button>
          )}
        </div>
      </form>

      <ProblemAlert error={logs.error} />
      {logs.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
      {!logs.isPending && rows.length === 0 && !logs.error && (
        <p className="text-sm text-muted-foreground">{filtered ? 'Nothing matches these filters.' : 'Nothing has been recorded yet.'}</p>
      )}

      <div data-testid="audit-log" className="space-y-6">
        {groupByDay(rows, tz).map((g) => (
          <section key={g.day} aria-labelledby={`audit-day-${g.day}`}>
            <h2 id={`audit-day-${g.day}`} className="mb-2 text-sm font-semibold text-muted-foreground">
              {dayHeading(g.day, today)}
            </h2>
            <ul className="space-y-2">
              {g.items.map((e) => (
                <EntryRow key={e.id} entry={e} timeZone={tz} />
              ))}
            </ul>
          </section>
        ))}
      </div>

      {logs.hasNextPage && (
        <Button variant="outline" disabled={logs.isFetchingNextPage} onClick={() => void logs.fetchNextPage()}>
          {logs.isFetchingNextPage ? 'Loading…' : 'Load more'}
        </Button>
      )}
    </div>
  )
}
