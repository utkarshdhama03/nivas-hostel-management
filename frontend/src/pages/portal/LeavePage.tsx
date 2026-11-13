// The student's leave (docs/04 §12.11, §12.18): ask for home leave or a local outing, see the
// decision, and cancel it until it has ended. Since D75 there is no gate pass and no personal QR.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '@/api/client'
import type { LeaveOut } from '@/api/types'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PageHeader, Select, Table, Td, Th } from '@/components/ui/table'
import { useOrgTimeZone } from '@/auth/session'
import { showWhen } from '@/lib/dates'


export function PortalLeavePage() {
  const tz = useOrgTimeZone()
  const queryClient = useQueryClient()
  const list = useQuery({ queryKey: ['my-leave'], queryFn: () => api<LeaveOut[]>('/portal/me/leave-requests') })
  const [form, setForm] = useState({ leave_type: 'home', reason: '', destination: '', starts_at: '', ends_at: '' })
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['my-leave'] })
  const create = useMutation({
    mutationFn: () =>
      api<LeaveOut>('/portal/me/leave-requests', {
        method: 'POST',
        body: {
          leave_type: form.leave_type,
          reason: form.reason,
          destination: form.destination || null,
          starts_at: new Date(form.starts_at).toISOString(),
          ends_at: new Date(form.ends_at).toISOString(),
        },
      }),
    onSuccess: () => {
      setForm({ ...form, reason: '', destination: '', starts_at: '', ends_at: '' })
      refresh()
    },
  })
  const cancel = useMutation({ mutationFn: (id: string) => api(`/portal/me/leave-requests/${id}/cancel`, { method: 'POST' }), onSuccess: refresh })
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value })
  return (
    <div className="max-w-3xl space-y-4">
      <PageHeader title="My leave" />
      <Card className="space-y-3">
        <CardTitle className="text-base">Ask for leave</CardTitle>
        <ProblemAlert error={create.error ?? cancel.error ?? list.error} />
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate()
          }}
        >
          <Field id="leave-type" label="Type">
            <Select id="leave-type" value={form.leave_type} onChange={set('leave_type')}>
              <option value="home">Home leave</option>
              <option value="local_outing">Local outing (same day)</option>
            </Select>
          </Field>
          <Field id="leave-destination" label="Destination (optional)">
            <Input id="leave-destination" value={form.destination} onChange={set('destination')} />
          </Field>
          <Field id="leave-start" label="Leaving">
            <Input id="leave-start" type="datetime-local" required value={form.starts_at} onChange={set('starts_at')} />
          </Field>
          <Field id="leave-end" label="Back by">
            <Input id="leave-end" type="datetime-local" required value={form.ends_at} onChange={set('ends_at')} />
          </Field>
          <div className="sm:col-span-2">
            <Field id="leave-reason" label="Reason">
              <Input id="leave-reason" required minLength={3} maxLength={500} value={form.reason} onChange={set('reason')} />
            </Field>
          </div>
          <div>
            <Button type="submit" disabled={create.isPending}>
              Send request
            </Button>
          </div>
        </form>
      </Card>
      {list.isSuccess && list.data.length === 0 && <p className="text-sm text-muted-foreground">You haven't asked for leave yet. Your requests and their answers will appear here.</p>}
      {(list.data?.length ?? 0) > 0 && (
      <Table data-testid="my-leave">
        <thead>
          <tr>
            <Th>Type</Th>
            <Th>From</Th>
            <Th>Until</Th>
            <Th>Status</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {(list.data ?? []).map((l) => (
            <tr key={l.id}>
              <Td>{l.leave_type.replace('_', ' ')}</Td>
              <Td>{showWhen(l.starts_at, tz)}</Td>
              <Td>{showWhen(l.ends_at, tz)}</Td>
              <Td>
                {l.status}
                {l.auto_approved && ' (automatic)'}
                {l.decision_note && <span className="block text-xs text-muted-foreground">{l.decision_note}</span>}
              </Td>
              <Td>
                {(l.status === 'pending' || l.status === 'approved') && (
                  <Button size="sm" variant="outline" onClick={() => cancel.mutate(l.id)}>
                    Cancel
                  </Button>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      )}
    </div>
  )
}
