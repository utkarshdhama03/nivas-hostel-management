// Mess menus (docs/04 §12.13; Q41, Q42): a week grid per hostel (4 meals × 7 days), saved in one
// go, and "copy a week" from another hostel or another week. Also the student's weekly view.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '@/api/client'
import type { HostelOut, MenuCopyOut, MenuEntryOut } from '@/api/types'
import { useOrgToday } from '@/auth/session'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { PageHeader, Select, Table, Td, Th } from '@/components/ui/table'
import { addDays, mondayOf } from '@/lib/dates'

const MEALS = ['breakfast', 'lunch', 'snacks', 'dinner'] as const

function days(start: string): string[] {
  return Array.from({ length: 7 }, (_, i) => addDays(start, i))
}
const dayName = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short', timeZone: 'UTC' })

export function MenuGrid({ start, entries, edit }: { start: string; entries: MenuEntryOut[]; edit?: { values: Record<string, string>; set: (k: string, v: string) => void } }) {
  const byKey = Object.fromEntries(entries.map((e) => [`${e.menu_date}|${e.meal}`, e.items]))
  return (
    <Table data-testid="menu-grid">
      <thead>
        <tr>
          <Th>Day</Th>
          {MEALS.map((m) => (
            <Th key={m} className="capitalize">
              {m}
            </Th>
          ))}
        </tr>
      </thead>
      <tbody>
        {days(start).map((d) => (
          <tr key={d}>
            <Td>{dayName(d)}</Td>
            {MEALS.map((m) => {
              const k = `${d}|${m}`
              return (
                <Td key={m}>
                  {edit ? <Input aria-label={`${d} ${m}`} value={edit.values[k] ?? byKey[k] ?? ''} onChange={(e) => edit.set(k, e.target.value)} /> : byKey[k] ?? '—'}
                </Td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

export function MessPage() {
  const queryClient = useQueryClient()
  const hostels = useQuery({ queryKey: ['hostels'], queryFn: () => api<HostelOut[]>('/admin/hostels') })
  const [picked, setPicked] = useState('')
  const hostelId = picked || hostels.data?.[0]?.id || ''
  const thisWeek = mondayOf(useOrgToday()) // the organization's week, not the browser's
  const [start, setStart] = useState(thisWeek)
  const end = days(start)[6]
  const menu = useQuery({
    queryKey: ['mess-menu', hostelId, start],
    enabled: !!hostelId,
    queryFn: () => api<MenuEntryOut[]>(`/admin/hostels/${hostelId}/mess-menu?start=${start}&end=${end}`),
  })
  const [values, setValues] = useState<Record<string, string>>({})
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['mess-menu'] })
  const save = useMutation({
    mutationFn: () =>
      api(`/admin/hostels/${hostelId}/mess-menu/entries`, {
        method: 'POST',
        body: {
          entries: Object.entries(values)
            .filter(([, v]) => v.trim())
            .map(([k, v]) => {
              const [menu_date, meal] = k.split('|')
              return { menu_date, meal, items: v.trim() }
            }),
        },
      }),
    onSuccess: () => {
      setValues({})
      refresh()
    },
  })
  const [copy, setCopy] = useState({ from_hostel_id: '', from_week_start: thisWeek, overwrite: false })
  const doCopy = useMutation({
    mutationFn: () => api<MenuCopyOut>(`/admin/hostels/${hostelId}/mess-menu/copy`, { method: 'POST', body: { ...copy, from_hostel_id: copy.from_hostel_id || null, to_week_start: start } }),
    onSuccess: refresh,
  })
  return (
    <div className="space-y-4">
      <PageHeader
        title="Mess menu"
        actions={
          <div className="flex gap-2">
            <Select
              aria-label="Hostel"
              value={hostelId}
              onChange={(e) => {
                setPicked(e.target.value)
                setValues({})
              }}
            >
              {(hostels.data ?? []).map((h) => (
                <option key={h.id} value={h.id}>
                  {h.code} · {h.name}
                </option>
              ))}
            </Select>
            <Input aria-label="Week starting" type="date" value={start} onChange={(e) => {
                if (!e.target.value) return
                setStart(mondayOf(e.target.value))
                setValues({})
              }} />
          </div>
        }
      />
      <ProblemAlert error={hostels.error ?? menu.error ?? save.error} />
      <MenuGrid start={start} entries={menu.data ?? []} edit={{ values, set: (k, v) => setValues((x) => ({ ...x, [k]: v })) }} />
      <Button disabled={Object.keys(values).length === 0 || save.isPending} onClick={() => save.mutate()}>
        Save menu
      </Button>
      <Card className="space-y-2">
        <CardTitle className="text-base">Copy a week into this one</CardTitle>
        <ProblemAlert error={doCopy.error} />
        <div className="flex flex-wrap items-center gap-2">
          <Select aria-label="From hostel" value={copy.from_hostel_id} onChange={(e) => setCopy({ ...copy, from_hostel_id: e.target.value })}>
            <option value="">This hostel</option>
            {(hostels.data ?? [])
              .filter((h) => h.id !== hostelId)
              .map((h) => (
                <option key={h.id} value={h.id}>
                  {h.code} · {h.name}
                </option>
              ))}
          </Select>
          <Input aria-label="From week starting" type="date" value={copy.from_week_start} onChange={(e) => e.target.value && setCopy({ ...copy, from_week_start: mondayOf(e.target.value) })} />
          <label className="flex items-center gap-1 text-sm">
            <input type="checkbox" checked={copy.overwrite} onChange={(e) => setCopy({ ...copy, overwrite: e.target.checked })} /> Overwrite existing
          </label>
          <Button variant="outline" onClick={() => doCopy.mutate()}>
            Copy
          </Button>
          {doCopy.data && (
            <span className="text-sm" data-testid="copy-result">
              Copied {doCopy.data.copied}, kept {doCopy.data.skipped}
            </span>
          )}
        </div>
      </Card>
    </div>
  )
}

export function PortalMessPage() {
  const start = mondayOf(useOrgToday())
  const menu = useQuery({ queryKey: ['my-mess', start], queryFn: () => api<MenuEntryOut[]>(`/portal/me/mess-menu?start=${start}&end=${days(start)[6]}`) })
  return (
    <div className="max-w-4xl space-y-4">
      <PageHeader title="Mess menu" />
      <ProblemAlert error={menu.error} />
      <MenuGrid start={start} entries={menu.data ?? []} />
    </div>
  )
}
