// The organization sign-in form (docs/04 §5.3-5.4), shared by /o/:slug/login and the public pages
// /sign-in/student and /sign-in/staff. The password alone signs in (D87: no two-step verification).
//
// On the public pages there is no organization in the address, so the form asks for the organization
// sign-in name too, pre-filled with the last one used in this browser (hms.lastOrg, see auth/session).
// An unknown organization gets the same answer as a wrong password: the server never says which
// organizations exist (05 AUTH-1).
//
// D78/D80: Organization sign-in (/sign-in/staff) and Student sign-in (/sign-in/student) ask for only the
// email and the password; the server finds the organization from the email. /o/:slug/login is unchanged.
import { useMutation } from '@tanstack/react-query'
import { type ReactNode, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { api, setAccessToken } from '@/api/client'
import type { AuthOut } from '@/api/types'
import { lastOrg, rememberOrg } from '@/auth/session'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'

export type Portal = 'staff' | 'student'

/** A sign-in name as typed: trimmed and lower-case ("Demo-Uni " -> "demo-uni"). */
export const cleanSlug = (value: string) => value.trim().toLowerCase()

/** After any successful sign-in: keep the access token in memory, remember the organization, open its area. */
export function useFinishSignIn() {
  const navigate = useNavigate()
  return (result: AuthOut, slug: string) => {
    setAccessToken('org', result.access_token ?? null)
    if (slug) rememberOrg(slug)
    navigate(result.user?.kind === 'student' ? '/portal' : '/admin', { replace: true })
  }
}

/** slug given: the organization is fixed (/o/:slug/login). slug absent: the form asks for it.
 *  header: shown above the fields. */
export function SignInForm({ portal, slug: fixedSlug, header }: { portal: Portal; slug?: string; header?: ReactNode }) {
  const finish = useFinishSignIn()
  const [orgName, setOrgName] = useState(() => fixedSlug ?? lastOrg() ?? '')
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  // D78/D80: the public sign-in pages have no organization field: email and password only.
  const emailOnly = fixedSlug === undefined
  const slug = emailOnly ? '' : cleanSlug(fixedSlug ?? orgName)
  const login = useMutation({
    mutationFn: () =>
      emailOnly
        ? api<AuthOut>('/auth/login', { method: 'POST', auth: false, body: { email: identifier, password, portal } })
        : api<AuthOut>(`/o/${encodeURIComponent(slug)}/auth/login`, { method: 'POST', auth: false, body: { portal, identifier, password } }),
    onSuccess: (r) => finish(r, slug),
  })
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        login.mutate()
      }}
    >
      {header}
      <ProblemAlert error={login.error} />
      {fixedSlug === undefined && !emailOnly && (
        <Field id="org-name" label="Organization sign-in name" hint="The short name your hostel office gave you, for example demo-uni.">
          <Input id="org-name" autoComplete="organization" autoCapitalize="none" spellCheck={false} value={orgName} onChange={(e) => setOrgName(e.target.value)} />
        </Field>
      )}
      <Field id="identifier" label={emailOnly ? 'Email' : portal === 'staff' ? 'Employee ID or email' : 'Student ID or email'}>
        <Input id="identifier" type={emailOnly ? 'email' : 'text'} autoComplete="username" value={identifier} onChange={(e) => setIdentifier(e.target.value)} />
      </Field>
      <Field id="password" label="Password">
        <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <Button type="submit" className="w-full" disabled={login.isPending || (!emailOnly && !slug) || !identifier || !password}>
        Sign in
      </Button>
      <p className="text-center text-sm">
        <Link
          className="text-primary underline"
          to={emailOnly ? (portal === 'staff' ? '/forgot-password/staff' : '/forgot-password') : `/o/${encodeURIComponent(fixedSlug ?? '')}/forgot-password`}
        >
          Forgot password?
        </Link>
      </p>
    </form>
  )
}
