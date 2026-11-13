// Complaints for Administrators (docs/04 §12.12; Q40, D75): the queue by target date, and one complaint with
// its photos, history and actions (assign to me, start, resolve with a note, comment).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '@/api/client'
import type { ComplaintDetail, ComplaintOut } from '@/api/types'
import { useMe, useOrgTimeZone } from '@/auth/session'
import { Photo } from '@/components/Photo'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { PageHeader, Select, Table, Td, Th } from '@/components/ui/table'
import { showDate, showWhen } from '@/lib/dates'

export const statusLabel = (s: string) => s.replace('_', ' ')

function Detail({ id }: { id: string }) {
  const tz = useOrgTimeZone()
  const queryClient = useQueryClient()
  const me = useMe()
  const c = useQuery({ queryKey: ['complaint', id], queryFn: () => api<ComplaintDetail>(`/admin/complaints/${id}`) })
  const [text, setText] = useState('')
  const act = useMutation({
    mutationFn: ({ action, body }: { action: string; body?: object }) => api<ComplaintDetail>(`/admin/complaints/${id}/${action}`, { method: 'POST', body }),
    onSuccess: () => {
      setText('')
      void queryClient.invalidateQueries({ queryKey: ['complaint', id] })
      void queryClient.invalidateQueries({ queryKey: ['complaints'] })
    },
  })
  const d = c.data
  if (!d) return <ProblemAlert error={c.error} />
  const active = d.status === 'open' || d.status === 'in_progress'
  return (
    <Card className="space-y-3" data-testid="complaint-detail">
      <CardTitle className="text-base">
        {d.title} · {d.priority} · {statusLabel(d.status)}
      </CardTitle>
      <ProblemAlert error={act.error} />
      <p className="text-sm">{d.description}</p>
      <p className="text-xs text-muted-foreground">
        {d.student_code} · {d.student_name} · {d.category} · target {showDate(d.target_date)}
        {d.assigned_to_name && ` · assigned to ${d.assigned_to_name}`}
        {d.reopen_count > 0 && ` · reopened ${d.reopen_count}×`}
      </p>
      {d.attachment_file_ids.length > 0 && (
        <div className="flex gap-2">
          {d.attachment_file_ids.map((f) => (
            <Photo key={f} fileId={f} alt="Complaint photo" size={96} />
          ))}
        </div>
      )}
      <ol className="space-y-1 text-sm">
        {d.updates.map((u) => (
          <li key={u.id} className="border-l-2 border-border pl-2">
            <span className="text-xs text-muted-foreground">{showWhen(u.created_at, tz)} </span>
            {u.to_status && <span className="font-medium">{u.from_status ? `${statusLabel(u.from_status)} → ` : ''}{statusLabel(u.to_status)} </span>}
            {u.comment}
          </li>
        ))}
      </ol>
      {d.status !== 'closed' && (
        <div className="flex flex-wrap items-center gap-2">
          {active && me.data && d.assigned_to !== me.data.id && (
            <Button size="sm" variant="outline" onClick={() => act.mutate({ action: 'assign', body: { user_id: me.data!.id } })}>
              Assign to me
            </Button>
          )}
          {d.status === 'open' && d.assigned_to && (
            <Button size="sm" variant="outline" onClick={() => act.mutate({ action: 'start' })}>
              Start work
            </Button>
          )}
          <Input aria-label="Note or comment" placeholder="Note or comment" className="w-72" value={text} onChange={(e) => setText(e.target.value)} />
          <Button size="sm" variant="outline" disabled={!text.trim()} onClick={() => act.mutate({ action: 'comments', body: { comment: text } })}>
            Comment
          </Button>
          {active && (
            <Button size="sm" disabled={text.trim().length < 3} onClick={() => act.mutate({ action: 'resolve', body: { note: text } })}>
              Resolve
            </Button>
          )}
        </div>
      )}
    </Card>
  )
}

export function ComplaintsPage() {
  const [status, setStatus] = useState('open')
  const list = useQuery({
    queryKey: ['complaints', status],
    queryFn: () => api<{ items: ComplaintOut[] }>(`/admin/complaints?page_size=100${status ? `&status=${status}` : ''}`),
  })
  const [chosen, setChosen] = useState<string | null>(null)
  return (
    <div className="space-y-4">
      <PageHeader
        title="Complaints"
        actions={
          <Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="open">Open</option>
            <option value="in_progress">In progress</option>
            <option value="resolved">Resolved</option>
            <option value="closed">Closed</option>
            <option value="">All</option>
          </Select>
        }
      />
      <ProblemAlert error={list.error} />
      <Table data-testid="complaints">
        <thead>
          <tr>
            <Th>Title</Th>
            <Th>Priority</Th>
            <Th>Target</Th>
            <Th>Status</Th>
          </tr>
        </thead>
        <tbody>
          {(list.data?.items ?? []).map((c) => (
            <tr key={c.id} className="cursor-pointer" onClick={() => setChosen(c.id)}>
              <Td className="underline">{c.title}</Td>
              <Td>{c.priority}</Td>
              <Td>{showDate(c.target_date)}</Td>
              <Td>{statusLabel(c.status)}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {chosen && <Detail key={chosen} id={chosen} />}
    </div>
  )
}
