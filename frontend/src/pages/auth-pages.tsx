/**
 * Pages used before sign-in (docs/04 §5.2-5.8): registration, email verification,
 * organization login (email and password, D87), forgot password (an emailed code, D77).
 * One-time link tokens arrive in the URL fragment (#token=...), which browsers never send to a server.
 */
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery } from '@tanstack/react-query'
import { type ReactNode, useContext, useEffect, useRef, useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link, useLocation, useParams } from 'react-router'
import { z } from 'zod'

import { api } from '@/api/client'
import type { AuthOut, PublicOrgOut } from '@/api/types'
import { lastOrg, SignedOutNotice } from '@/auth/session'
import { cleanSlug, type Portal, SignInForm, useFinishSignIn } from '@/components/SignInForm'
import { ProblemAlert } from '@/components/ProblemAlert'
import { InPublicLayout } from '@/pages/public/context'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'

/** A card with a title. On its own (/o/:slug/login and friends) it is the whole page, centred in a
 *  <main>. Inside the public layout (the sidebar pages), that layout already has the <main>. */
export function AuthLayout({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  const card = (
    <Card className="w-full max-w-md space-y-6">
      <div>
        <CardTitle>{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </div>
      {children}
    </Card>
  )
  if (useContext(InPublicLayout)) return <div className="flex justify-center">{card}</div>
  return <main className="flex min-h-screen items-center justify-center p-4">{card}</main>
}

function fragmentToken(hash: string): string {
  return new URLSearchParams(hash.replace(/^#/, '')).get('token') ?? ''
}

// --- registration (the front page is pages/public/LandingPage.tsx) ------------------------------------

const registrationSchema = z.object({
  name: z.string().trim().min(2, 'At least 2 characters').max(120),
  slug: z
    .string()
    .trim()
    .toLowerCase()
    .regex(/^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$/, '3-40 characters: lower-case letters, digits and hyphens'),
  contact_email: z.email('Enter a valid email'),
  full_name: z.string().trim().min(1, 'Required').max(120),
  email: z.email('Enter a valid email'),
  password: z.string().min(8, 'At least 8 characters').max(1024),
})
type Registration = z.infer<typeof registrationSchema>

export function RegisterPage() {
  const form = useForm<Registration>({ resolver: zodResolver(registrationSchema) })
  const register = useMutation({
    mutationFn: (v: Registration) =>
      api('/public/organizations', {
        method: 'POST',
        auth: false,
        body: {
          organization: { name: v.name, slug: v.slug, contact_email: v.contact_email },
          super_admin: { full_name: v.full_name, email: v.email, password: v.password },
        },
      }),
  })
  const e = form.formState.errors
  if (register.isSuccess) {
    return (
      <AuthLayout title="Check your email">
        <Alert variant="success">We sent a confirmation link to {form.getValues('email')}. Open it to activate your organization.</Alert>
      </AuthLayout>
    )
  }
  return (
    <AuthLayout title="Register your organization" description="You will be its administrator: the one account that runs it.">
      <form className="space-y-4" onSubmit={form.handleSubmit((v) => register.mutate(v))} noValidate>
        <ProblemAlert error={register.error} />
        <Field id="name" label="Organization name" error={e.name?.message}>
          <Input id="name" aria-invalid={!!e.name} {...form.register('name')} />
        </Field>
        <Field id="org-slug" label="Organization sign-in name" error={e.slug?.message} hint="Students and staff type it to sign in, e.g. sunrise-hostels">
          <Input id="org-slug" aria-invalid={!!e.slug} {...form.register('slug')} />
        </Field>
        <Field id="contact_email" label="Office email" error={e.contact_email?.message}>
          <Input id="contact_email" type="email" aria-invalid={!!e.contact_email} {...form.register('contact_email')} />
        </Field>
        <Field id="full_name" label="Your full name" error={e.full_name?.message}>
          <Input id="full_name" aria-invalid={!!e.full_name} {...form.register('full_name')} />
        </Field>
        <Field id="email" label="Your email" error={e.email?.message}>
          <Input id="email" type="email" autoComplete="username" aria-invalid={!!e.email} {...form.register('email')} />
        </Field>
        <Field id="password" label="Password" error={e.password?.message} hint="A long phrase is easiest to remember. No symbol rules.">
          <Input id="password" type="password" autoComplete="new-password" aria-invalid={!!e.password} {...form.register('password')} />
        </Field>
        <Button type="submit" className="w-full" disabled={register.isPending}>
          Register
        </Button>
      </form>
    </AuthLayout>
  )
}

export function VerifyRegistrationPage() {
  const { slug = '' } = useParams()
  const location = useLocation()
  const finish = useFinishSignIn()
  const started = useRef(false)
  const verify = useMutation({
    mutationFn: () => api<AuthOut>(`/o/${slug}/auth/verify-registration`, { method: 'POST', auth: false, body: { token: fragmentToken(location.hash) } }),
    onSuccess: (r) => finish(r, slug), // D87: the confirmed email signs the administrator in
  })
  useEffect(() => {
    if (!started.current) {
      started.current = true
      verify.mutate()
    }
  }, [verify])

  return (
    <AuthLayout title="Activate your organization">
      {verify.isPending && <p className="text-sm text-muted-foreground">Checking your link…</p>}
      <ProblemAlert error={verify.error} />
    </AuthLayout>
  )
}

// --- login --------------------------------------------------------------------------------------------

export function LoginPage() {
  const { slug = '' } = useParams()
  const org = useQuery({ queryKey: ['public-org', slug], queryFn: () => api<PublicOrgOut>(`/public/organizations/${slug}`, { auth: false }), retry: false })
  const [portal, setPortal] = useState<Portal>('staff')

  if (org.isError) {
    return (
      <AuthLayout title="Organization not found">
        <p className="text-sm">
          Check the address, or{' '}
          <Link className="text-primary underline" to="/">
            try another
          </Link>
          .
        </p>
      </AuthLayout>
    )
  }
  return (
    <AuthLayout title={org.data ? `Sign in to ${org.data.name}` : 'Sign in'}>
      <SignInForm
        portal={portal}
        slug={slug}
        header={
          <>
            <SignedOutNotice />
            <div className="grid grid-cols-2 gap-1 rounded-lg bg-muted p-1" role="tablist">
              {(['staff', 'student'] as const).map((p) => (
                <button
                  key={p}
                  type="button"
                  role="tab"
                  aria-selected={portal === p}
                  className={`rounded-md py-1.5 text-sm ${portal === p ? 'bg-card font-medium shadow-sm' : 'text-muted-foreground'}`}
                  onClick={() => setPortal(p)}
                >
                  {p === 'staff' ? 'Organization' : 'Student'}
                </button>
              ))}
            </div>
          </>
        }
      />
    </AuthLayout>
  )
}

// --- password reset (D77: an emailed 6-digit code, no link) --------------------------------------------

/** Forgot password, in two steps on one page: (1) organization + Employee ID, Student ID or email ->
 *  "if the account exists, we sent a code"; (2) the code + a new password. The server answers step 1
 *  the same whether or not the account exists, and step 2 the same for every kind of wrong code. */
/** portal: the public sign-in pages' version (D78 staff, D80 student): only the email; the server finds the
 *  organization. Without it (/o/:slug/forgot-password): the organization and the email or ID. */
export function ForgotPasswordPage({ portal }: { portal?: Portal } = {}) {
  const staff = portal !== undefined // email only
  const { slug: fixedSlug } = useParams()
  const [orgName, setOrgName] = useState(() => fixedSlug ?? lastOrg() ?? '')
  const [identifier, setIdentifier] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const slug = cleanSlug(orgName)
  const base = staff ? '/auth/password-reset' : `/o/${encodeURIComponent(slug)}/auth/password-reset`
  const who = staff ? { email: identifier, portal } : { identifier }
  const request = useMutation({
    mutationFn: () => api(`${base}/request`, { method: 'POST', auth: false, body: who }),
  })
  const confirm = useMutation({
    mutationFn: () =>
      api(`${base}/confirm`, {
        method: 'POST',
        auth: false,
        body: { ...who, code: code.replace(/\s/g, ''), new_password: password },
      }),
  })

  if (confirm.isSuccess) {
    return (
      <AuthLayout title="Password changed">
        <Alert variant="success">
          Password changed. All your devices were signed out.{' '}
          <Link className="underline" to={staff ? `/sign-in/${portal}` : `/o/${encodeURIComponent(slug)}/login`}>
            Sign in
          </Link>
        </Alert>
      </AuthLayout>
    )
  }
  if (request.isSuccess) {
    return (
      <AuthLayout title="Enter the code" description="Then choose your new password.">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            confirm.mutate()
          }}
        >
          <Alert variant="success">If an account with an email address matches, we sent it a 6-digit code. It works once, for 10 minutes.</Alert>
          <ProblemAlert error={confirm.error} />
          <Field id="reset-code" label="6-digit code from the email">
            <Input id="reset-code" inputMode="numeric" autoComplete="one-time-code" maxLength={12} value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
          <Field id="new-password" label="New password" hint="At least 8 characters. A phrase works well.">
            <Input id="new-password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Button type="submit" className="w-full" disabled={!code || !password || confirm.isPending}>
            Save password
          </Button>
          <p className="text-center text-sm">
            <button
              type="button"
              className="text-primary underline"
              onClick={() => {
                setCode('')
                confirm.reset()
                request.reset()
              }}
            >
              Send a new code
            </button>
          </p>
        </form>
      </AuthLayout>
    )
  }
  return (
    <AuthLayout title="Reset your password" description="We email you a 6-digit code.">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          request.mutate()
        }}
      >
        <ProblemAlert error={request.error} />
        {!staff && (
          <Field id="org-name" label="Organization sign-in name" hint="The short name your hostel office gave you, for example demo-uni.">
            <Input id="org-name" autoComplete="organization" autoCapitalize="none" spellCheck={false} value={orgName} onChange={(e) => setOrgName(e.target.value)} />
          </Field>
        )}
        <Field id="identifier" label={staff ? 'Email' : 'Employee ID, Student ID or email'}>
          <Input id="identifier" type={staff ? 'email' : 'text'} autoComplete="username" value={identifier} onChange={(e) => setIdentifier(e.target.value)} />
        </Field>
        <Button type="submit" className="w-full" disabled={(!staff && !slug) || !identifier || request.isPending}>
          Send code
        </Button>
      </form>
    </AuthLayout>
  )
}
