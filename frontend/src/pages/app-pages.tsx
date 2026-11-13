/**
 * Signed-in areas (docs/04 §2): /admin for staff, /portal for students, /platform for the
 * Platform Owner. Phase 1 has the shell, the notification bell and the Security page; the
 * modules fill the shells from Phase 2 on.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LogOut } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router'

import { api, logout, setAccessToken } from '@/api/client'
import type { AuthOut, PlatformMeOut, SessionOut } from '@/api/types'
import { lastOrg, SignedOutNotice, useMe, useOrgTimeZone } from '@/auth/session'
import { NotificationBell } from '@/components/NotificationBell'
import { ProblemAlert } from '@/components/ProblemAlert'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { AuthLayout } from '@/pages/auth-pages'
import { showWhen } from '@/lib/dates'

export function OrgShell({ area }: { area: 'admin' | 'portal' }) {
  const me = useMe()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  // Menu items the signed-in person can't use are hidden (convenience only: the API enforces everything).
  // Since D75/D76 the one staff account is the organization's Administrator, who sees them all.
  const perms = new Set(me.data?.permissions ?? [])
  const nav = [
    { to: `/${area}`, label: 'Home', end: true, show: true },
    { to: '/admin/hostels', label: 'Hostels', end: false, show: area === 'admin' && perms.has('hostels.view') },
    { to: '/admin/students', label: 'Students', end: false, show: area === 'admin' && perms.has('students.view') },
    { to: '/admin/registration-requests', label: 'Registration requests', end: false, show: area === 'admin' && perms.has('students.manage') },
    { to: '/admin/allocations', label: 'Allocations', end: false, show: area === 'admin' && perms.has('students.view') },
    { to: '/admin/fee-structures', label: 'Fees', end: false, show: area === 'admin' && perms.has('finance.view') },
    { to: '/admin/payment-queues', label: 'Payment queues', end: false, show: area === 'admin' && perms.has('finance.manage') },
    { to: '/admin/leave', label: 'Leave', end: false, show: area === 'admin' && perms.has('leave.manage') },
    { to: '/admin/complaints', label: 'Complaints', end: false, show: area === 'admin' && perms.has('complaints.manage') },
    { to: '/admin/mess', label: 'Mess menu', end: false, show: area === 'admin' && perms.has('mess.manage') },
    { to: '/admin/announcements', label: 'Announcements', end: false, show: area === 'admin' && perms.has('announcements.manage') },
    { to: '/admin/staff', label: 'Administrator', end: false, show: area === 'admin' && perms.has('staff.manage') },
    { to: '/portal/fees', label: 'My fees', end: false, show: area === 'portal' },
    { to: '/portal/leave', label: 'My leave', end: false, show: area === 'portal' },
    { to: '/portal/complaints', label: 'My complaints', end: false, show: area === 'portal' },
    { to: '/portal/mess', label: 'Mess menu', end: false, show: area === 'portal' },
    { to: '/portal/announcements', label: 'Announcements', end: false, show: area === 'portal' },
    { to: '/portal/profile', label: 'My profile', end: false, show: area === 'portal' },
    { to: '/admin/organization', label: 'Organization', end: false, show: area === 'admin' && perms.has('organization.manage') },
    { to: '/admin/audit', label: 'Audit log', end: false, show: area === 'admin' && perms.has('audit.read') },
    { to: `/${area}/security`, label: 'Security', end: false, show: true },
  ].filter((n) => n.show)
  return (
    <div className="min-h-screen">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-card px-4 py-3 md:px-6">
        <div>
          <p className="text-sm font-semibold">{me.data?.organization.name ?? '…'}</p>
          <p className="text-xs text-muted-foreground">{area === 'admin' ? 'Administration' : 'Student portal'}</p>
        </div>
        <div className="flex items-center gap-3">
          <NotificationBell />
          <ThemeToggle />
          <span className="text-sm" data-testid="signed-in-user">
            {me.data?.full_name}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={async () => {
              const slug = me.data?.organization.slug ?? lastOrg()
              await logout('org')
              queryClient.clear()
              navigate(slug ? `/o/${slug}/login` : '/', { replace: true })
            }}
          >
            <LogOut size={14} /> Sign out
          </Button>
        </div>
      </header>
      <div className="flex flex-col md:flex-row">
        <nav aria-label="Menu" className="flex gap-1 overflow-x-auto border-b border-border px-3 py-2 md:block md:w-52 md:shrink-0 md:space-y-1 md:overflow-visible md:border-b-0 md:border-r md:p-4">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) => `block shrink-0 whitespace-nowrap rounded-md px-3 py-2 text-sm md:whitespace-normal ${isActive ? 'bg-muted font-medium' : 'text-muted-foreground hover:bg-muted'}`}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <main className="min-w-0 flex-1 p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

// --- security page -----------------------------------------------------------------------------------

/** A browser's user-agent string in words: "Chrome on Windows". The full string stays in the tooltip. */
export function deviceName(ua: string | null | undefined): string {
  if (!ua) return 'Unknown device'
  const browser = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : null
  const os = /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iPhone or iPad' : /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) ? 'Mac' : /Linux/.test(ua) ? 'Linux' : null
  if (browser && os) return `${browser} on ${os}`
  return browser ?? os ?? 'Unknown device'
}

export function SecurityPage() {
  const tz = useOrgTimeZone()
  const queryClient = useQueryClient()
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: () => api<{ sessions: SessionOut[] }>('/auth/sessions') })
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/auth/sessions/${id}/revoke`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sessions'] }),
  })
  const revokeOthers = useMutation({
    mutationFn: () => api('/auth/sessions/revoke-others', { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sessions'] }),
  })
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const change = useMutation({ mutationFn: () => api('/auth/password/change', { method: 'POST', body: { current_password: current, new_password: next } }) })

  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-2xl font-semibold">Security</h1>
      <Card className="space-y-3">
        <CardTitle className="text-base">Where you are signed in</CardTitle>
        <ProblemAlert error={revoke.error ?? revokeOthers.error} />
        <ul className="divide-y divide-border text-sm">
          {sessions.data?.sessions.map((s) => (
            <li key={s.id} className="flex items-center justify-between py-2">
              <span>
                <span title={s.user_agent ?? undefined}>{deviceName(s.user_agent)}</span> · signed in {showWhen(s.created_at, tz)}
                {s.current && <strong className="ml-2">(this device)</strong>}
              </span>
              {!s.current && (
                <Button size="sm" variant="ghost" onClick={() => revoke.mutate(s.id)}>
                  Sign out
                </Button>
              )}
            </li>
          ))}
        </ul>
        <Button variant="outline" size="sm" onClick={() => revokeOthers.mutate()}>
          Sign out everywhere else
        </Button>
      </Card>
      <Card>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            change.mutate()
          }}
        >
          <CardTitle className="text-base">Change password</CardTitle>
          <ProblemAlert error={change.error} />
          {change.isSuccess && <Alert variant="success">Password changed. Your other devices were signed out.</Alert>}
          <Field id="current-password" label="Current password">
            <Input id="current-password" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
          </Field>
          <Field id="new-password" label="New password" hint="At least 8 characters. A phrase works well.">
            <Input id="new-password" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
          </Field>
          <Button type="submit" disabled={!current || !next || change.isPending}>
            Change password
          </Button>
        </form>
      </Card>
    </div>
  )
}

// --- platform ------------------------------------------------------------------------------------------

export function PlatformLoginPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const done = (r: AuthOut) => {
    setAccessToken('platform', r.access_token ?? null)
    navigate('/platform', { replace: true })
  }
  const login = useMutation({
    mutationFn: () => api<AuthOut>('/platform/auth/login', { method: 'POST', realm: 'platform', auth: false, body: { email, password } }),
    onSuccess: done, // D87: the password alone signs in
  })
  return (
    <AuthLayout title="Platform administration">
      <SignedOutNotice />
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          login.mutate()
        }}
      >
        <ProblemAlert error={login.error} />
        <Field id="email" label="Email">
          <Input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field id="password" label="Password">
          <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Button type="submit" className="w-full" disabled={!email || !password || login.isPending}>
          Sign in
        </Button>
      </form>
    </AuthLayout>
  )
}

const PLATFORM_NAV = [
  ['/platform', 'Organizations'],
  ['/platform/health', 'Health'],
  ['/platform/audit', 'Audit log'],
  ['/platform/settings', 'Settings'],
] as const

export function PlatformShell({ children }: { children?: ReactNode }) {
  const navigate = useNavigate()
  const me = useQuery({ queryKey: ['platform-me'], queryFn: () => api<PlatformMeOut>('/platform/me', { realm: 'platform' }) })
  return (
    <div className="min-h-screen">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-card px-4 py-3 md:px-6">
        <p className="text-sm font-semibold">Platform administration</p>
        <div className="flex items-center gap-3">
          <ThemeToggle />
          <span className="text-sm">{me.data?.full_name}</span>
          <Button
            variant="outline"
            size="sm"
            onClick={async () => {
              await logout('platform')
              navigate('/platform/login', { replace: true })
            }}
          >
            <LogOut size={14} /> Sign out
          </Button>
        </div>
      </header>
      <div className="flex flex-col md:flex-row">
        <nav aria-label="Menu" className="flex gap-1 overflow-x-auto border-b border-border px-3 py-2 md:block md:w-52 md:shrink-0 md:space-y-1 md:overflow-visible md:border-b-0 md:border-r md:p-4">
          {PLATFORM_NAV.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === '/platform'} className={({ isActive }) => `block shrink-0 whitespace-nowrap rounded-md px-3 py-2 text-sm md:whitespace-normal ${isActive ? 'bg-muted font-medium' : 'hover:bg-muted'}`}>
              {label}
            </NavLink>
          ))}
        </nav>
        <main className="min-w-0 flex-1 p-4 md:p-6">{children ?? <Outlet />}</main>
      </div>
    </div>
  )
}

export function NotFoundPage() {
  return (
    <AuthLayout title="Page not found">
      <Link className="text-primary underline" to="/">
        Go to the start page
      </Link>
    </AuthLayout>
  )
}
