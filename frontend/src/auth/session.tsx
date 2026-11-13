/**
 * Session state for the UI. The tokens themselves live in api/client.ts (memory only).
 *
 * <RequireSession realm="org" kind="staff"> wraps every signed-in page:
 *   1. token in memory?          render the page
 *   2. no token (e.g. reload)?   try refresh() once with the httpOnly cookie
 *   3. still nothing?            go to the login page
 * The UI check is only for convenience; the API enforces everything (docs/02 §7).
 */
import { useQuery } from '@tanstack/react-query'
import { type ReactNode, useEffect, useState, useSyncExternalStore } from 'react'
import { Navigate, useLocation } from 'react-router'

import { api, getAccessToken, onTokenChange, refresh, type Realm } from '@/api/client'
import type { MeOut } from '@/api/types'
import { Alert } from '@/components/ui/alert'
import { dateIn } from '@/lib/dates'

const LAST_ORG_KEY = 'hms.lastOrg' // the organization slug (not secret): where to send a signed-out user

export function rememberOrg(slug: string): void {
  try {
    localStorage.setItem(LAST_ORG_KEY, slug)
  } catch {
    /* storage disabled: fine */
  }
}

export function lastOrg(): string | null {
  try {
    return localStorage.getItem(LAST_ORG_KEY)
  } catch {
    return null
  }
}

export function useAccessToken(realm: Realm): string | null {
  return useSyncExternalStore(onTokenChange, () => getAccessToken(realm))
}

export function useMe(enabled = true) {
  const token = useAccessToken('org')
  return useQuery({ queryKey: ['me', token !== null], queryFn: () => api<MeOut>('/me'), enabled: enabled && token !== null })
}

/** The organization's time zone (Q48), for showWhen/showDay in lib/dates.ts. */
export function useOrgTimeZone(): string {
  return useMe().data?.organization.timezone ?? 'Asia/Kolkata'
}

/** Today's date in the organization's time zone, 'YYYY-MM-DD' (Q48). Not the UTC date: see lib/dates.ts. */
export function useOrgToday(): string {
  return dateIn(useMe().data?.organization.timezone ?? 'Asia/Kolkata')
}

// The scanner screens' 15-minute idle sign-out (Q67) went with the gate and attendance (D75).
const SIGNED_OUT: Record<string, string> = {
  signed_out: 'Your session has ended. Please sign in again.',
}

/** On a sign-in page: why the user is here, when they were sent from a signed-in page (Q91). */
export function SignedOutNotice() {
  const reason = (useLocation().state as { reason?: string } | null)?.reason
  const text = reason ? SIGNED_OUT[reason] : undefined
  return text ? <Alert>{text}</Alert> : null
}

export function RequireSession({ realm, kind, children }: { realm: Realm; kind?: 'staff' | 'student'; children: ReactNode }) {
  const token = useAccessToken(realm)
  const location = useLocation()
  const [tried, setTried] = useState(false)

  useEffect(() => {
    if (token === null && !tried) {
      void refresh(realm).finally(() => setTried(true))
    }
  }, [token, tried, realm])

  const me = useMe(realm === 'org' && token !== null)

  if (token === null) {
    if (!tried) return <FullPageMessage>Loading…</FullPageMessage>
    const slug = lastOrg()
    const to = realm === 'platform' ? '/platform/login' : slug ? `/o/${slug}/login` : '/'
    // Q91: the refresh rule stays strict; the sign-in page says why you're there (SignedOutNotice).
    return <Navigate to={to} replace state={{ from: location.pathname, reason: 'signed_out' }} />
  }
  if (realm === 'org' && kind) {
    if (!me.data) return <FullPageMessage>Loading…</FullPageMessage>
    if (me.data.kind !== kind) return <Navigate to={me.data.kind === 'staff' ? '/admin' : '/portal'} replace />
  }
  return <>{children}</>
}

export function FullPageMessage({ children }: { children: ReactNode }) {
  return <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">{children}</div>
}
