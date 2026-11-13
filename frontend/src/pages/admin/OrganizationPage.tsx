// Organization settings and the academic calendar (docs/04 §12.4). Administrators only.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '@/api/client'
import type { AcademicSessionOut, OrganizationOut } from '@/api/types'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PageHeader, Select, Table, Td, Th } from '@/components/ui/table'
import { showDate } from '@/lib/dates'

type ProfileForm = Record<string, string>
const PROFILE_FIELDS: [string, string][] = [
  ['name', 'Organization name'],
  ['timezone', 'Timezone (e.g. Asia/Kolkata)'],
  ['legal_name', 'Legal name'],
  ['contact_email', 'Office email'],
  ['contact_phone', 'Office phone'],
  ['address_line1', 'Address'],
  ['city', 'City'],
  ['state', 'State'],
  ['postal_code', 'PIN code'],
  ['upi_id', 'UPI ID (for student payments)'],
  ['upi_payee_name', 'UPI payee name'],
]

function OrganizationForm({ org }: { org: OrganizationOut }) {
  const queryClient = useQueryClient()
  const initial: ProfileForm = Object.fromEntries(
    PROFILE_FIELDS.map(([k]) => [k, String((k in org ? org[k as keyof OrganizationOut] : org.profile[k as keyof OrganizationOut['profile']]) ?? '')]),
  )
  const [form, setForm] = useState(initial)
  const save = useMutation({
    mutationFn: () => {
      const changed = Object.fromEntries(Object.entries(form).filter(([k, v]) => v !== initial[k]).map(([k, v]) => [k, v === '' ? null : v]))
      return api<OrganizationOut>('/admin/organization', { method: 'PATCH', body: { version: org.version, ...changed } })
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['organization'] }),
  })
  return (
    <form
      className="grid gap-3 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate()
      }}
    >
      <div className="sm:col-span-2">
        <ProblemAlert error={save.error} />
        {save.isSuccess && <Alert variant="success">Saved.</Alert>}
      </div>
      {PROFILE_FIELDS.map(([key, label]) => (
        <Field key={key} id={`org-${key}`} label={label}>
          <Input id={`org-${key}`} value={form[key] ?? ''} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
        </Field>
      ))}
      <div className="sm:col-span-2">
        <Button type="submit" disabled={save.isPending}>
          Save settings
        </Button>
      </div>
    </form>
  )
}

/** D74: whether students may ask to register on the public site. Off by default. */
function StudentRequestsSwitch({ org }: { org: OrganizationOut }) {
  const queryClient = useQueryClient()
  const on = org.profile.accepts_student_requests
  const save = useMutation({
    mutationFn: (value: boolean) =>
      api<OrganizationOut>('/admin/organization', { method: 'PATCH', body: { version: org.version, accepts_student_requests: value } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['organization'] }),
  })
  return (
    <div className="space-y-2">
      <ProblemAlert error={save.error} />
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" id="accepts-student-requests" checked={on} disabled={save.isPending} onChange={(e) => save.mutate(e.target.checked)} />
        Accept student registration requests
      </label>
      <p className="text-sm text-muted-foreground">
        {on
          ? 'Students can ask to register on the public site (Student registration). You approve or reject each request under Registration requests.'
          : 'Off: the public Student registration form says this organization does not take requests.'}
      </p>
    </div>
  )
}

function AcademicSessions() {
  const queryClient = useQueryClient()
  const sessions = useQuery({ queryKey: ['academic-sessions'], queryFn: () => api<AcademicSessionOut[]>('/admin/academic-sessions') })
  const [draft, setDraft] = useState({ name: '', starts_on: '', ends_on: '' })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['academic-sessions'] })
  const create = useMutation({ mutationFn: () => api('/admin/academic-sessions', { method: 'POST', body: draft }), onSuccess: refresh })
  const makeCurrent = useMutation({ mutationFn: (id: string) => api(`/admin/academic-sessions/${id}/make-current`, { method: 'POST' }), onSuccess: refresh })
  return (
    <div className="space-y-3">
      <ProblemAlert error={create.error ?? makeCurrent.error} />
      <Table>
        <thead>
          <tr>
            <Th>Session</Th>
            <Th>Dates</Th>
            <Th>Terms</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {sessions.data?.map((s) => (
            <tr key={s.id}>
              <Td>
                {s.name} {s.is_current && <strong className="ml-1 text-success">current</strong>}
              </Td>
              <Td>
                {showDate(s.starts_on)} – {showDate(s.ends_on)}
              </Td>
              <Td>{s.terms?.map((t) => t.name).join(', ') || '—'}</Td>
              <Td>
                {!s.is_current && (
                  <Button size="sm" variant="outline" onClick={() => makeCurrent.mutate(s.id)}>
                    Make current
                  </Button>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      <form
        className="grid items-end gap-2 sm:grid-cols-4"
        onSubmit={(e) => {
          e.preventDefault()
          create.mutate()
        }}
      >
        <Field id="s-name" label="Name">
          <Input id="s-name" placeholder="2026-27" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        </Field>
        <Field id="s-start" label="Starts">
          <Input id="s-start" type="date" value={draft.starts_on} onChange={(e) => setDraft({ ...draft, starts_on: e.target.value })} />
        </Field>
        <Field id="s-end" label="Ends">
          <Input id="s-end" type="date" value={draft.ends_on} onChange={(e) => setDraft({ ...draft, ends_on: e.target.value })} />
        </Field>
        <Button type="submit" disabled={create.isPending}>
          Add session
        </Button>
      </form>
      {!!sessions.data?.length && <AddTerm sessions={sessions.data} onAdded={refresh} />}
    </div>
  )
}

/** Terms inside a session (Q18): "per term" fees charge by them (review Critical 8). */
function AddTerm({ sessions, onAdded }: { sessions: AcademicSessionOut[]; onAdded: () => void }) {
  const [t, setT] = useState({ session_id: sessions.find((s) => s.is_current)?.id ?? sessions[0]!.id, name: '', starts_on: '', ends_on: '' })
  const add = useMutation({
    mutationFn: () => api(`/admin/academic-sessions/${t.session_id}/terms`, { method: 'POST', body: { name: t.name, starts_on: t.starts_on, ends_on: t.ends_on } }),
    onSuccess: () => {
      setT({ ...t, name: '', starts_on: '', ends_on: '' })
      onAdded()
    },
  })
  return (
    <form
      className="grid items-end gap-2 sm:grid-cols-5"
      onSubmit={(e) => {
        e.preventDefault()
        add.mutate()
      }}
    >
      <div className="sm:col-span-5">
        <ProblemAlert error={add.error} />
      </div>
      <Field id="t-session" label="Term session">
        <Select id="t-session" value={t.session_id} onChange={(e) => setT({ ...t, session_id: e.target.value })}>
          {sessions.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </Select>
      </Field>
      <Field id="t-name" label="Term name">
        <Input id="t-name" placeholder="Semester 1" required value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} />
      </Field>
      <Field id="t-start" label="Term starts">
        <Input id="t-start" type="date" required value={t.starts_on} onChange={(e) => setT({ ...t, starts_on: e.target.value })} />
      </Field>
      <Field id="t-end" label="Term ends">
        <Input id="t-end" type="date" required value={t.ends_on} onChange={(e) => setT({ ...t, ends_on: e.target.value })} />
      </Field>
      <Button type="submit" variant="outline" disabled={add.isPending}>
        Add term
      </Button>
    </form>
  )
}

export function OrganizationPage() {
  const org = useQuery({ queryKey: ['organization'], queryFn: () => api<OrganizationOut>('/admin/organization') })
  return (
    <div className="max-w-4xl space-y-6">
      <PageHeader title="Organization" />
      <Card className="space-y-4">
        <CardTitle className="text-base">Settings</CardTitle>
        {org.data && <OrganizationForm key={org.data.version} org={org.data} />}
        {org.data && (
          <div className="mt-6 border-t border-border pt-4">
            <h2 className="mb-2 font-medium">Student registration</h2>
            <StudentRequestsSwitch org={org.data} />
          </div>
        )}
      </Card>
      <Card className="space-y-4">
        <CardTitle className="text-base">Academic sessions</CardTitle>
        <AcademicSessions />
      </Card>
    </div>
  )
}
