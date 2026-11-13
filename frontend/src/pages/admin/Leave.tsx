// Leave (docs/04 §12.11, D75). The Administrators' leave queue: approve, reject with a note, and record
// emergency leave for a student. Since D75 approved leave is a record only: no gate pass, no scans.
// Every rule is enforced by the API; this page only shows what it allows.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '@/api/client'
import type { LeaveListItem, LeaveOut, StudentListItem } from '@/api/types'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PageHeader, Select, Table, Td, Th } from '@/components/ui/table'
import { useOrgTimeZone } from '@/auth/session'
import { showWhen } from '@/lib/dates'


function StudentPicker({ value, onChange, id }: { value: string; onChange: (id: string) => void; id: string }) {
  const [q, setQ] = useState('')
  const list = useQuery({
    queryKey: ['students-pick', q],
    queryFn: () => api<{ items: StudentListItem[] }>(`/admin/students?page_size=50&status=active${q.length >= 2 ? `&q=${encodeURIComponent(q)}` : ''}`),
  })
  return (
    <div className="flex flex-wrap gap-2">
      <Input aria-label="Search students" placeholder="Search name or ID" value={q} onChange={(e) => setQ(e.target.value)} className="w-48" />
      <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} className="w-64 max-w-full">
        <option value="">Choose a student…</option>
        {(list.data?.items ?? []).map((s) => (
          <option key={s.id} value={s.id}>
            {s.student_code} · {s.full_name}
          </option>
        ))}
      </Select>
    </div>
  )
}

export function LeaveQueuePage() {
  const tz = useOrgTimeZone()
  const queryClient = useQueryClient()
  const [status, setStatus] = useState('pending')
  const list = useQuery({
    queryKey: ['leave-queue', status],
    queryFn: () => api<{ items: LeaveListItem[] }>(`/admin/leave-requests?page_size=100${status ? `&status=${status}` : ''}`),
  })
  const [notes, setNotes] = useState<Record<string, string>>({})
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['leave-queue'] })
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approve' | 'reject' }) =>
      api<LeaveOut>(`/admin/leave-requests/${id}/${action}`, { method: 'POST', body: { note: notes[id] || null } }),
    onSuccess: refresh,
  })
  const [em, setEm] = useState({ student_id: '', reason: '', starts_at: '', ends_at: '' })
  const emergency = useMutation({
    mutationFn: () =>
      api<LeaveOut>('/admin/leave-requests', {
        method: 'POST',
        body: { ...em, starts_at: new Date(em.starts_at).toISOString(), ends_at: new Date(em.ends_at).toISOString() },
      }),
    onSuccess: () => {
      setEm({ student_id: '', reason: '', starts_at: '', ends_at: '' })
      refresh()
    },
  })
  return (
    <div className="space-y-4">
      <PageHeader
        title="Leave requests"
        actions={
          <Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="cancelled">Cancelled</option>
            <option value="">All</option>
          </Select>
        }
      />
      <ProblemAlert error={decide.error ?? list.error} />
      <Table data-testid="leave-queue">
        <thead>
          <tr>
            <Th>Student</Th>
            <Th>Type</Th>
            <Th>From</Th>
            <Th>Until</Th>
            <Th>Reason</Th>
            <Th>Status</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {(list.data?.items ?? []).map((l) => (
            <tr key={l.id}>
              <Td>
                {l.student_code} · {l.student_name}
              </Td>
              <Td>{l.leave_type.replace('_', ' ')}</Td>
              <Td>{showWhen(l.starts_at, tz)}</Td>
              <Td>{showWhen(l.ends_at, tz)}</Td>
              <Td>
                {l.reason}
                {l.destination && <span className="block text-xs text-muted-foreground">{l.destination}</span>}
              </Td>
              <Td>{l.status}</Td>
              <Td>
                {l.status === 'pending' && (
                  <div className="flex flex-wrap items-center gap-2">
                    <Input aria-label="Note" placeholder="Note (needed to reject)" className="w-48" value={notes[l.id] ?? ''} onChange={(e) => setNotes({ ...notes, [l.id]: e.target.value })} />
                    <Button size="sm" onClick={() => decide.mutate({ id: l.id, action: 'approve' })}>
                      Approve
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => decide.mutate({ id: l.id, action: 'reject' })}>
                      Reject
                    </Button>
                  </div>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      <Card className="space-y-3">
        <CardTitle className="text-base">Emergency leave for a student</CardTitle>
        <ProblemAlert error={emergency.error} />
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault()
            emergency.mutate()
          }}
        >
          <div className="sm:col-span-2">
            <Field id="em-student" label="Student">
              <StudentPicker id="em-student" value={em.student_id} onChange={(student_id) => setEm({ ...em, student_id })} />
            </Field>
          </div>
          <Field id="em-start" label="Leaving">
            <Input id="em-start" type="datetime-local" required value={em.starts_at} onChange={(e) => setEm({ ...em, starts_at: e.target.value })} />
          </Field>
          <Field id="em-end" label="Back by">
            <Input id="em-end" type="datetime-local" required value={em.ends_at} onChange={(e) => setEm({ ...em, ends_at: e.target.value })} />
          </Field>
          <div className="sm:col-span-2">
            <Field id="em-reason" label="Reason">
              <Input id="em-reason" required minLength={3} value={em.reason} onChange={(e) => setEm({ ...em, reason: e.target.value })} />
            </Field>
          </div>
          <div>
            <Button type="submit" disabled={!em.student_id || emergency.isPending}>
              Create emergency leave
            </Button>
          </div>
        </form>
      </Card>
    </div>
  )
}
