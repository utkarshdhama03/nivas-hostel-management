// The first page after sign-in (D86). The Administrator sees a few counts from /admin/overview (no
// charts: analytics was removed); each count opens the page where it can be acted on. A student
// sees short links to their own pages. Both keep the "Welcome, <name>" heading.
import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link } from 'react-router'

import { api } from '@/api/client'
import type { OverviewOut } from '@/api/types'
import { useMe } from '@/auth/session'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Card } from '@/components/ui/card'

function Welcome() {
  const me = useMe()
  return (
    <h1 className="text-2xl font-semibold" data-testid="dashboard-title">
      Welcome, {me.data?.full_name}
    </h1>
  )
}

function Tile({ to, label, children, testId }: { to: string; label: string; children: ReactNode; testId?: string }) {
  return (
    <Link to={to} className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" data-testid={testId}>
      <Card className="h-full space-y-1 p-4 transition-colors hover:bg-muted">
        <p className="text-sm text-muted-foreground">{label}</p>
        <div className="text-2xl font-semibold">{children}</div>
      </Card>
    </Link>
  )
}

export function AdminHomePage() {
  const o = useQuery({ queryKey: ['overview'], queryFn: () => api<OverviewOut>('/admin/overview') })
  const d = o.data
  const n = (v: number | undefined) => (v === undefined ? '…' : v.toLocaleString('en-IN'))
  return (
    <div className="max-w-5xl space-y-6">
      <Welcome />
      <ProblemAlert error={o.error} />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" data-testid="overview">
        <Tile to="/admin/students" label="Students" testId="overview-students">
          {n(d?.students)}
        </Tile>
        <Tile to="/admin/hostels" label="Beds free / occupied" testId="overview-beds">
          {n(d?.beds_free)} <span className="text-base font-normal text-muted-foreground">free</span> · {n(d?.beds_occupied)}{' '}
          <span className="text-base font-normal text-muted-foreground">occupied</span>
        </Tile>
        <Tile to="/admin/registration-requests" label="Registration requests waiting" testId="overview-requests">
          {n(d?.registration_requests_pending)}
        </Tile>
        <Tile to="/admin/leave" label="Leave requests waiting" testId="overview-leave">
          {n(d?.leave_pending)}
        </Tile>
        <Tile to="/admin/complaints" label="Open complaints" testId="overview-complaints">
          {n(d?.complaints_open)}
        </Tile>
      </div>
    </div>
  )
}

const STUDENT_LINKS: { to: string; label: string; text: string }[] = [
  { to: '/portal/fees', label: 'My fees', text: 'What is due, receipts, and paying by UPI.' },
  { to: '/portal/leave', label: 'My leave', text: 'Ask for home leave or a local outing, and see the answer.' },
  { to: '/portal/complaints', label: 'My complaints', text: 'Report a problem and follow it until it is fixed.' },
  { to: '/portal/mess', label: 'Mess menu', text: "This week's menu for your hostel." },
  { to: '/portal/announcements', label: 'Announcements', text: 'Notices from the hostel office.' },
]

export function PortalHomePage() {
  return (
    <div className="max-w-5xl space-y-6">
      <Welcome />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {STUDENT_LINKS.map((l) => (
          <Link key={l.to} to={l.to} className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <Card className="h-full space-y-1 p-4 transition-colors hover:bg-muted">
              <p className="font-medium">{l.label}</p>
              <p className="text-sm text-muted-foreground">{l.text}</p>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  )
}
