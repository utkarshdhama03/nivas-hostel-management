// Bed allocation UI (docs/04 §12.8): allocate or reserve a vacant bed from the student's page,
// act on the current stay (check in, check out, cancel, transfer), see the history; and the
// Allocations page: the bed map (default, pages/admin/BedMap.tsx) or the list with a search. The
// database decides double bookings: a lost race shows the server's "bed was just taken" message.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useSearchParams } from 'react-router'

import { api } from '@/api/client'
import type { AllocationListItem, AllocationOut, BedBoard } from '@/api/types'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { BedMap } from '@/pages/admin/BedMap'
import { PageHeader, Select, Table, Td, Th } from '@/components/ui/table'
import { showDate, showWhen } from '@/lib/dates'
import { useOrgTimeZone } from '@/auth/session'

function useVacantBeds(hostelId: string) {
  const board = useQuery({ queryKey: ['bed-board', hostelId], queryFn: () => api<BedBoard>(`/admin/hostels/${hostelId}/bed-board`) })
  const beds = (board.data?.rooms ?? []).flatMap((r) => r.beds.filter((b) => b.state === 'vacant').map((b) => ({ id: b.id, label: `${r.building}-${r.number} / ${b.label} (${r.room_type})` })))
  return { beds, error: board.error }
}

export function StudentAllocation({ studentId, hostelId, active }: { studentId: string; hostelId: string; active: boolean }) {
  const tz = useOrgTimeZone()
  const queryClient = useQueryClient()
  const history = useQuery({ queryKey: ['allocations', studentId], queryFn: () => api<AllocationOut[]>(`/admin/students/${studentId}/allocations`) })
  const { beds, error } = useVacantBeds(hostelId)
  const [bed, setBed] = useState('')
  const [mode, setMode] = useState<'assign' | 'reserve'>('assign')
  const [start, setStart] = useState('')
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['allocations', studentId] })
    void queryClient.invalidateQueries({ queryKey: ['bed-board', hostelId] })
  }
  const create = useMutation({
    mutationFn: () => api('/admin/allocations', { method: 'POST', body: { student_id: studentId, bed_id: bed, mode, start_date: start || null } }),
    onSuccess: () => {
      setBed('')
      refresh()
    },
  })
  const act = useMutation({
    mutationFn: ({ id, action, body }: { id: string; action: string; body?: object }) => api(`/admin/allocations/${id}/${action}`, { method: 'POST', body }),
    onSuccess: refresh,
  })
  const current = history.data?.find((a) => a.status === 'active' || a.status === 'reserved')
  return (
    <Card className="space-y-3">
      <CardTitle className="text-base">Bed</CardTitle>
      <ProblemAlert error={create.error ?? act.error ?? error ?? history.error} />
      {current ? (
        <div className="space-y-2 text-sm">
          <p data-testid="current-allocation">
            {current.status === 'active' ? 'Staying' : 'Reserved'} since {showDate(current.start_date)}
            {current.reservation_expires_at && current.status === 'reserved' && ` · reservation expires ${showWhen(current.reservation_expires_at, tz)}`}
          </p>
          <div className="flex flex-wrap gap-2">
            {current.status === 'reserved' && (
              <>
                <Button size="sm" onClick={() => act.mutate({ id: current.id, action: 'check-in' })}>
                  Check in
                </Button>
                <Button size="sm" variant="outline" onClick={() => act.mutate({ id: current.id, action: 'cancel' })}>
                  Cancel reservation
                </Button>
              </>
            )}
            {current.status === 'active' && (
              <>
                <Button size="sm" variant="outline" onClick={() => act.mutate({ id: current.id, action: 'check-out' })}>
                  Check out
                </Button>
                <Select aria-label="Transfer to bed" className="w-64" value={bed} onChange={(e) => setBed(e.target.value)}>
                  <option value="">Transfer to…</option>
                  {beds.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.label}
                    </option>
                  ))}
                </Select>
                <Button size="sm" variant="outline" disabled={!bed} onClick={() => act.mutate({ id: current.id, action: 'transfer', body: { bed_id: bed } })}>
                  Transfer
                </Button>
              </>
            )}
          </div>
        </div>
      ) : (
        active && (
          <div className="flex flex-wrap items-end gap-2">
            <Field id="al-bed" label="Vacant bed">
              <Select id="al-bed" className="w-64" value={bed} onChange={(e) => setBed(e.target.value)}>
                <option value="">Choose…</option>
                {beds.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field id="al-mode" label="Action">
              <Select id="al-mode" value={mode} onChange={(e) => setMode(e.target.value as 'assign' | 'reserve')}>
                <option value="assign">Allocate now</option>
                <option value="reserve">Reserve</option>
              </Select>
            </Field>
            <Field id="al-start" label="From (optional)">
              <Input id="al-start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
            </Field>
            <Button disabled={!bed || create.isPending} onClick={() => create.mutate()}>
              {mode === 'assign' ? 'Allocate' : 'Reserve'}
            </Button>
          </div>
        )
      )}
      {!!history.data?.length && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">History ({history.data.length})</summary>
          <ul className="mt-1 space-y-1">
            {history.data.map((a) => (
              <li key={a.id}>
                {showDate(a.start_date)} → {a.end_date ? showDate(a.end_date) : 'open'} · {a.status}
                {a.end_reason && ` (${a.end_reason})`}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  )
}

function AllocationsList() {
  const [status, setStatus] = useState('active')
  const [q, setQ] = useState('')
  const list = useQuery({
    queryKey: ['allocations-list', status, q],
    queryFn: () => api<{ items: AllocationListItem[] }>(`/admin/allocations?page_size=100${status ? `&status=${status}` : ''}${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ''}`),
  })
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <Field id="al-search" label="Search" hint="Student name, Student ID or room number.">
          <Input id="al-search" className="w-64" value={q} onChange={(e) => setQ(e.target.value)} />
        </Field>
        <Field id="al-status" label="Status">
          <Select id="al-status" className="w-40" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            {['active', 'reserved', 'ended', 'cancelled'].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
        </Field>
      </div>
      <ProblemAlert error={list.error} />
      <Table>
        <thead>
          <tr>
            <Th>Student</Th>
            <Th>Bed</Th>
            <Th>Dates</Th>
            <Th>Status</Th>
          </tr>
        </thead>
        <tbody>
          {list.data?.items.map((a) => (
            <tr key={a.id}>
              <Td>
                {a.student_name} <span className="text-xs text-muted-foreground">{a.student_code}</span>
              </Td>
              <Td>{a.bed}</Td>
              <Td>
                {showDate(a.start_date)} → {a.end_date ? showDate(a.end_date) : 'open'}
              </Td>
              <Td>{a.status}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {list.isSuccess && list.data.items.length === 0 && <p className="text-sm text-muted-foreground">Nothing matches.</p>}
    </div>
  )
}

/** Allocations: the bed map by default; the list (with search) one click away. ?view=list keeps it in the address. */
export function AllocationsPage() {
  const [params, setParams] = useSearchParams()
  const view = params.get('view') === 'list' ? 'list' : 'map'
  const choose = (v: 'map' | 'list') => setParams(v === 'map' ? {} : { view: 'list' }, { replace: true })
  return (
    <div className="max-w-7xl space-y-4">
      <PageHeader
        title="Allocations"
        actions={
          <div role="group" aria-label="View" className="inline-flex rounded-lg border border-border bg-card p-0.5">
            {(['map', 'list'] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                onClick={() => choose(v)}
                className={`rounded-md px-3 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${view === v ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'}`}
              >
                {v === 'map' ? 'Bed map' : 'List'}
              </button>
            ))}
          </div>
        }
      />
      {view === 'map' ? <BedMap /> : <AllocationsList />}
    </div>
  )
}
