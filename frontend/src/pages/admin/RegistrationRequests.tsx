// Student registration requests (D74): the Administrator reads a request (with the photo the student
// sent), then approves it (the office's own admission makes the student and the Student ID) or rejects
// it with a reason the student sees. Pending ones first, oldest first; decided ones newest first.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'

import { api, apiBlob } from '@/api/client'
import type { RegistrationApprovedOut, HostelOut, RegistrationRequestListItem, RegistrationRequestOut } from '@/api/types'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PageHeader, Table, Td, Th } from '@/components/ui/table'
import { useMe } from '@/auth/session'
import { dateIn, showDate } from '@/lib/dates'

type Status = 'pending' | 'approved' | 'rejected'
const TABS: { value: Status; label: string }[] = [
  { value: 'pending', label: 'Waiting' },
  { value: 'approved', label: 'Approved' },
  { value: 'rejected', label: 'Rejected' },
]

function RequestPhoto({ id }: { id: string }) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    let made: string | null = null
    let live = true
    apiBlob(`/admin/registration-requests/${id}/photo`)
      .then((blob) => {
        made = URL.createObjectURL(blob)
        if (live) setUrl(made)
      })
      .catch(() => setUrl(null))
    return () => {
      live = false
      if (made) URL.revokeObjectURL(made)
    }
  }, [id])
  if (!url) return <div className="h-40 w-32 rounded-lg bg-muted" />
  return <img src={url} alt="The photo the student sent" className="h-40 w-32 rounded-lg object-cover" />
}

/** An instant shown as the organization's calendar date. */
function useOrgDate(): (instant: string) => string {
  const zone = useMe().data?.organization.timezone ?? 'Asia/Kolkata'
  return (instant) => showDate(dateIn(zone, new Date(instant)))
}

function Details({ id, hostels, onDone }: { id: string; hostels: Map<string, string>; onDone: () => void }) {
  const orgDate = useOrgDate()
  const qc = useQueryClient()
  const request = useQuery({ queryKey: ['registration-request', id], queryFn: () => api<RegistrationRequestOut>(`/admin/registration-requests/${id}`) })
  const [reason, setReason] = useState('')
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['registration-requests'] })
    void qc.invalidateQueries({ queryKey: ['registration-request', id] })
  }
  const approve = useMutation({
    mutationFn: () => api<RegistrationApprovedOut>(`/admin/registration-requests/${id}/approve`, { method: 'POST' }),
    onSuccess: refresh,
  })
  const reject = useMutation({
    mutationFn: () => api<RegistrationRequestOut>(`/admin/registration-requests/${id}/reject`, { method: 'POST', body: { reason } }),
    onSuccess: refresh,
  })
  const r = request.data
  if (!r) return <ProblemAlert error={request.error} />
  const row = (label: string, value: string | number | null | undefined) => (
    <div className="grid grid-cols-3 gap-2 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="col-span-2">{value ?? '—'}</dd>
    </div>
  )
  return (
    <Card className="space-y-4" data-testid="registration-request">
      <div className="flex items-start justify-between gap-4">
        <CardTitle>{r.full_name ?? 'Personal data deleted'}</CardTitle>
        <Button variant="outline" size="sm" onClick={onDone}>
          Close
        </Button>
      </div>
      <div className="flex flex-col gap-4 sm:flex-row">
        {r.has_photo && <RequestPhoto id={r.id} />}
        <dl className="flex-1 space-y-1">
          {row('Hostel', hostels.get(r.hostel_id))}
          {row('Email', r.email)}
          {row('Phone', r.phone)}
          {row('Gender', r.gender)}
          {row('Date of birth', r.date_of_birth ? showDate(r.date_of_birth) : null)}
          {row('Course', r.program)}
          {row('Year of study', r.year_of_study)}
          {row('Parent or guardian', r.guardian_name ? `${r.guardian_name} (${r.guardian_relationship ?? ''})` : null)}
          {row("Guardian's phone", r.guardian_phone)}
          {row('Asked on', orgDate(r.created_at))}
          {r.reject_reason && row('Reason for rejection', r.reject_reason)}
        </dl>
      </div>
      <ProblemAlert error={approve.error ?? reject.error} />
      {approve.data && (
        <Alert variant="success">
          Approved. Student ID {approve.data.student_code}.{' '}
          <Link className="underline" to={`/admin/students/${approve.data.student_id}`}>
            Open the student
          </Link>
          . The student can sign in now and was told by email.
        </Alert>
      )}
      {reject.isSuccess && <Alert variant="success">Rejected. The student was told by email, with your reason.</Alert>}
      {r.status === 'pending' && !approve.data && !reject.isSuccess && (
        <div className="space-y-3 border-t border-border pt-4">
          <Button onClick={() => approve.mutate()} disabled={approve.isPending || reject.isPending}>
            Approve and admit
          </Button>
          <Field id="reject-reason" label="Reason for rejecting (the student sees it)" hint="5 to 500 characters.">
            <Input id="reject-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <Button variant="outline" onClick={() => reject.mutate()} disabled={reason.trim().length < 5 || approve.isPending || reject.isPending}>
            Reject
          </Button>
        </div>
      )}
    </Card>
  )
}

export function RegistrationRequestsPage() {
  const orgDate = useOrgDate()
  const [status, setStatus] = useState<Status>('pending')
  const [open, setOpen] = useState<string | null>(null)
  const list = useQuery({
    queryKey: ['registration-requests', status],
    queryFn: () => api<{ items: RegistrationRequestListItem[] }>(`/admin/registration-requests?status=${status}&page_size=100`),
  })
  const hostels = useQuery({ queryKey: ['hostels'], queryFn: () => api<HostelOut[]>('/admin/hostels') })
  const names = new Map((hostels.data ?? []).map((h) => [h.id, h.name]))
  return (
    <div className="max-w-5xl space-y-6">
      <PageHeader title="Registration requests" />
      <p className="text-sm text-muted-foreground">
        Students who asked to register on the public site. Turn requests on or off on the{' '}
        <Link className="text-primary underline" to="/admin/organization">
          Organization
        </Link>{' '}
        page.
      </p>
      <div className="grid w-fit grid-cols-3 gap-1 rounded-lg bg-muted p-1" role="tablist" aria-label="Requests">
        {TABS.map((t) => (
          <button
            key={t.value}
            type="button"
            role="tab"
            aria-selected={status === t.value}
            className={`rounded-md px-3 py-1.5 text-sm ${status === t.value ? 'bg-card font-medium shadow-sm' : 'text-muted-foreground'}`}
            onClick={() => {
              setStatus(t.value)
              setOpen(null)
            }}
          >
            {t.label}
          </button>
        ))}
      </div>
      <ProblemAlert error={list.error} />
      {open && <Details id={open} hostels={names} onDone={() => setOpen(null)} />}
      <Table>
        <thead>
          <tr>
            <Th>Name</Th>
            <Th>Email</Th>
            <Th>Hostel</Th>
            <Th>{status === 'pending' ? 'Asked on' : 'Decided on'}</Th>
            <Th>
              <span className="sr-only">Open</span>
            </Th>
          </tr>
        </thead>
        <tbody>
          {list.data?.items.map((r) => (
            <tr key={r.id}>
              <Td>{r.full_name ?? 'Personal data deleted'}</Td>
              <Td>{r.email ?? '—'}</Td>
              <Td>{names.get(r.hostel_id) ?? '—'}</Td>
              <Td>{orgDate(status === 'pending' ? r.created_at : (r.decided_at ?? r.created_at))}</Td>
              <Td>
                <Button variant="outline" size="sm" onClick={() => setOpen(r.id)}>
                  Open
                </Button>
              </Td>
            </tr>
          ))}
          {list.data?.items.length === 0 && (
            <tr>
              <Td colSpan={5} className="text-muted-foreground">
                {status === 'pending' ? 'No requests waiting.' : 'None.'}
              </Td>
            </tr>
          )}
        </tbody>
      </Table>
    </div>
  )
}
