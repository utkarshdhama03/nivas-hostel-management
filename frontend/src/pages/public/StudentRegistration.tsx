// Student registration (D74, updated 11 November): a student asks to join an organization that takes
// requests; the Administrator approves or rejects. There is no status page: after confirming the email,
// the student signs in on Student sign-in with the request's email and password, and the sign-in form
// says how the request stands (waiting, or rejected with the reason).
import { useMutation, useQuery } from '@tanstack/react-query'
import { type ChangeEvent, type ComponentProps, type FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router'

import { api, apiUpload } from '@/api/client'
import type { RegistrationInfoOut } from '@/api/types'
import { lastOrg } from '@/auth/session'
import { ProblemAlert } from '@/components/ProblemAlert'
import { cleanSlug } from '@/components/SignInForm'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/table'
import { AuthLayout } from '@/pages/auth-pages'

const RELATIONSHIPS = ['father', 'mother', 'guardian', 'sibling', 'spouse', 'other'] as const
const label = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

type Form = {
  hostel_id: string
  full_name: string
  email: string
  phone: string
  gender: string
  date_of_birth: string
  program: string
  year_of_study: string
  guardian_name: string
  guardian_relationship: string
  guardian_phone: string
  password: string
  confirm: string
  website: string // the honeypot: hidden from people; bots fill it in
}

const EMPTY: Form = {
  hostel_id: '',
  full_name: '',
  email: '',
  phone: '',
  gender: 'male',
  date_of_birth: '',
  program: '',
  year_of_study: '',
  guardian_name: '',
  guardian_relationship: 'father',
  guardian_phone: '',
  password: '',
  confirm: '',
  website: '',
}

export function StudentRegistrationPage() {
  const [orgName, setOrgName] = useState(() => lastOrg() ?? '')
  const [chosen, setChosen] = useState('') // the organization whose form is shown
  const info = useQuery({
    queryKey: ['student-registration', chosen],
    queryFn: () => api<RegistrationInfoOut>(`/public/organizations/${encodeURIComponent(chosen)}/student-registration`, { auth: false }),
    enabled: chosen !== '',
  })
  const [f, setF] = useState<Form>(EMPTY)
  const [photo, setPhoto] = useState<File | null>(null)
  const [mismatch, setMismatch] = useState(false)
  const set = (k: keyof Form) => (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value })
  const send = useMutation({
    mutationFn: () => {
      const form = new FormData()
      for (const [k, v] of Object.entries(f)) if (k !== 'confirm' && v !== '') form.append(k, v)
      if (photo) form.append('photo', photo)
      return apiUpload(`/public/organizations/${encodeURIComponent(chosen)}/student-registration`, form)
    },
  })

  if (send.isSuccess) {
    return (
      <AuthLayout title="Check your email">
        <Alert variant="success">
          We sent a confirmation link to {f.email}. Open it within 24 hours. Then the administrator looks at your request.
        </Alert>
        <p className="text-sm text-muted-foreground">
          To see how your request stands, sign in on{' '}
          <Link className="text-primary underline" to="/sign-in/student">
            Student sign-in
          </Link>{' '}
          with your email and the password you chose.
        </p>
      </AuthLayout>
    )
  }

  if (!info.data?.accepts_requests) {
    return (
      <AuthLayout title="Student registration" description="Ask your hostel to admit you. The administrator approves or rejects the request.">
        <form
          className="space-y-4"
          onSubmit={(e: FormEvent) => {
            e.preventDefault()
            setChosen(cleanSlug(orgName))
          }}
        >
          <ProblemAlert error={info.error} />
          {info.data && !info.data.accepts_requests && (
            <Alert variant="error">This organization doesn&apos;t take registration requests. Check the sign-in name, or ask your hostel office.</Alert>
          )}
          <Field id="reg-org" label="Organization sign-in name" hint="The short name your hostel gave you, for example demo-uni.">
            <Input id="reg-org" autoCapitalize="none" spellCheck={false} value={orgName} onChange={(e) => setOrgName(e.target.value)} />
          </Field>
          <Button type="submit" className="w-full" disabled={!orgName.trim() || info.isFetching}>
            Continue
          </Button>
        </form>
      </AuthLayout>
    )
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (f.password !== f.confirm) {
      setMismatch(true)
      return
    }
    setMismatch(false)
    send.mutate()
  }
  const text = (k: keyof Form, labelText: string, props: Partial<ComponentProps<typeof Input>> = {}, hint?: string) => (
    <Field id={`reg-${k}`} label={labelText} hint={hint}>
      <Input id={`reg-${k}`} value={f[k]} onChange={set(k)} {...props} />
    </Field>
  )
  return (
    <AuthLayout title={`Register at ${info.data.organization_name ?? ''}`} description="Fill in your details. The administrator approves or rejects the request.">
      <form className="space-y-4" onSubmit={submit} noValidate>
        <ProblemAlert error={send.error} />
        <Field id="reg-hostel" label="Hostel">
          <Select id="reg-hostel" value={f.hostel_id} onChange={set('hostel_id')} required>
            <option value="">Choose…</option>
            {info.data.hostels.map((h) => (
              <option key={h.id} value={h.id}>
                {h.name}
                {h.resident_type ? ` (${h.resident_type})` : ''}
              </option>
            ))}
          </Select>
        </Field>
        {text('full_name', 'Full name', { autoComplete: 'name', required: true })}
        {text('email', 'Email', { type: 'email', autoComplete: 'email', required: true }, 'You sign in with it. We send a confirmation link to it.')}
        {text('phone', 'Phone (optional)', { type: 'tel', autoComplete: 'tel' })}
        <Field id="reg-gender" label="Gender">
          <Select id="reg-gender" value={f.gender} onChange={set('gender')}>
            <option value="male">Male</option>
            <option value="female">Female</option>
            <option value="other">Other</option>
          </Select>
        </Field>
        {text('date_of_birth', 'Date of birth', { type: 'date', required: true })}
        {text('program', 'Course (optional)', {}, 'For example B.Tech Computer Science')}
        {text('year_of_study', 'Year of study (optional)', { type: 'number', min: 1, max: 10, inputMode: 'numeric' })}
        {text('guardian_name', "Parent or guardian's name", { required: true })}
        <Field id="reg-guardian_relationship" label="Relationship">
          <Select id="reg-guardian_relationship" value={f.guardian_relationship} onChange={set('guardian_relationship')}>
            {RELATIONSHIPS.map((r) => (
              <option key={r} value={r}>
                {label(r)}
              </option>
            ))}
          </Select>
        </Field>
        {text('guardian_phone', "Parent or guardian's phone", { type: 'tel', required: true })}
        <Field id="reg-photo" label="Your photo" hint="A clear face photo, JPEG or PNG, up to 5 MB.">
          <Input id="reg-photo" type="file" accept="image/jpeg,image/png" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} required />
        </Field>
        {text('password', 'Password', { type: 'password', autoComplete: 'new-password', required: true }, 'At least 15 characters. A long phrase is easiest to remember.')}
        {text('confirm', 'Password again', { type: 'password', autoComplete: 'new-password', required: true })}
        {mismatch && <Alert variant="error">The two passwords are different.</Alert>}
        {/* The honeypot: off-screen, not reachable by keyboard, ignored by screen readers. */}
        <div aria-hidden="true" className="absolute -left-[10000px] h-px w-px overflow-hidden">
          <label htmlFor="reg-website">Website</label>
          <input id="reg-website" name="website" tabIndex={-1} autoComplete="off" value={f.website} onChange={set('website')} />
        </div>
        <Button
          type="submit"
          className="w-full"
          disabled={send.isPending || !f.hostel_id || !f.full_name || !f.email || !f.date_of_birth || !f.guardian_name || !f.guardian_phone || !photo || !f.password}
        >
          Send request
        </Button>
      </form>
    </AuthLayout>
  )
}

/** The emailed link: /o/:slug/register/verify#token=… The token stays in the fragment (never sent to a server log). */
export function VerifyStudentRequestPage() {
  const { slug = '' } = useParams()
  const location = useLocation()
  const started = useRef(false)
  const verify = useMutation({
    mutationFn: () =>
      api(`/o/${encodeURIComponent(slug)}/auth/student-registration/verify`, {
        method: 'POST',
        auth: false,
        body: { token: new URLSearchParams(location.hash.replace(/^#/, '')).get('token') ?? '' },
      }),
  })
  useEffect(() => {
    if (!started.current) {
      started.current = true
      verify.mutate()
    }
  }, [verify])
  return (
    <AuthLayout title="Confirm your email">
      {verify.isPending && <p className="text-sm text-muted-foreground">Checking your link…</p>}
      <ProblemAlert error={verify.error} />
      {verify.isSuccess && (
        <>
          <Alert variant="success">Email confirmed. Your request is now waiting for the administrator&apos;s approval.</Alert>
          <p className="text-sm text-muted-foreground">
            To see how it stands, sign in on{' '}
            <Link className="text-primary underline" to="/sign-in/student">
              Student sign-in
            </Link>{' '}
            with your email and password.
          </p>
        </>
      )}
    </AuthLayout>
  )
}
