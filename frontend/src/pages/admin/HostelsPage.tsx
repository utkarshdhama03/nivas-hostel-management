// Hostels and their structure (docs/04 §12.5): list, create, buildings -> floors -> rooms -> beds,
// room types, the bed board and the rooms-and-beds CSV import.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { Link, useParams } from 'react-router'

import { api, apiUpload } from '@/api/client'
import type { BedBoard, BuildingOut, FloorOut, HostelOut, ImportResult, RoomOut, RoomTypeOut } from '@/api/types'
import { useMe } from '@/auth/session'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PageHeader, Select, Table, Td, Th } from '@/components/ui/table'

const STATE_STYLE: Record<string, string> = {
  vacant: 'bg-success/15 border-success/40',
  occupied: 'bg-primary/15 border-primary/40',
  reserved: 'bg-yellow-400/20 border-yellow-500/40',
  maintenance: 'bg-orange-400/20 border-orange-500/40',
  inactive: 'bg-muted border-border text-muted-foreground',
}

export function HostelsPage() {
  const me = useMe()
  const queryClient = useQueryClient()
  const hostels = useQuery({ queryKey: ['hostels'], queryFn: () => api<HostelOut[]>('/admin/hostels') })
  const [draft, setDraft] = useState({ code: '', name: '', resident_type: 'boys' })
  const create = useMutation({
    mutationFn: () => api<HostelOut>('/admin/hostels', { method: 'POST', body: draft }),
    onSuccess: () => {
      setDraft({ code: '', name: '', resident_type: 'boys' })
      void queryClient.invalidateQueries({ queryKey: ['hostels'] })
    },
  })
  const canCreate = me.data?.permissions.includes('hostels.create')
  return (
    <div className="max-w-4xl space-y-6">
      <PageHeader title="Hostels" />
      <Table>
        <thead>
          <tr>
            <Th>Code</Th>
            <Th>Name</Th>
            <Th>Residents</Th>
          </tr>
        </thead>
        <tbody>
          {hostels.data?.map((h) => (
            <tr key={h.id}>
              <Td>{h.code}</Td>
              <Td>
                <Link className="text-primary underline" to={`/admin/hostels/${h.id}`}>
                  {h.name}
                </Link>
              </Td>
              <Td>{h.resident_type ?? '—'}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {canCreate && (
        <Card className="space-y-3">
          <CardTitle className="text-base">Add a hostel</CardTitle>
          <ProblemAlert error={create.error} />
          <form
            className="grid items-end gap-2 sm:grid-cols-4"
            onSubmit={(e) => {
              e.preventDefault()
              create.mutate()
            }}
          >
            <Field id="h-code" label="Code">
              <Input id="h-code" value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value.toUpperCase() })} />
            </Field>
            <Field id="h-name" label="Name">
              <Input id="h-name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </Field>
            <Field id="h-type" label="Residents">
              <Select id="h-type" value={draft.resident_type} onChange={(e) => setDraft({ ...draft, resident_type: e.target.value })}>
                <option value="boys">Boys</option>
                <option value="girls">Girls</option>
                <option value="coed">Co-ed</option>
              </Select>
            </Field>
            <Button type="submit" disabled={create.isPending}>
              Create
            </Button>
          </form>
        </Card>
      )}
    </div>
  )
}

function BedBoardView({ hostelId }: { hostelId: string }) {
  const board = useQuery({ queryKey: ['bed-board', hostelId], queryFn: () => api<BedBoard>(`/admin/hostels/${hostelId}/bed-board`), refetchInterval: 30_000 })
  if (board.error) return <ProblemAlert error={board.error} />
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground" data-testid="bed-counts">
        {Object.entries(board.data?.counts ?? {})
          .map(([k, v]) => `${v} ${k}`)
          .join(' · ') || 'No beds yet'}
      </p>
      <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4">
        {board.data?.rooms.map((r) => (
          <div key={r.id} className="rounded-lg border border-border p-2 text-sm">
            <p className="font-medium">
              {r.building}-{r.number} <span className="text-xs text-muted-foreground">({r.room_type})</span>
            </p>
            <div className="mt-1 flex flex-wrap gap-1">
              {r.beds.map((b) => (
                <span key={b.id} title={b.state} className={`rounded border px-2 py-0.5 text-xs ${STATE_STYLE[b.state] ?? ''}`}>
                  {b.label}: {b.state}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function CsvImport({ hostelId }: { hostelId: string }) {
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const upload = useMutation({
    mutationFn: () => {
      const data = new FormData()
      data.append('file', file as File)
      return apiUpload<ImportResult>(`/admin/hostels/${hostelId}/imports/rooms-beds`, data)
    },
    onSuccess: () => queryClient.invalidateQueries(),
  })
  return (
    <div className="space-y-2">
      <p className="text-sm text-muted-foreground">
        CSV with the header <code>building,floor,room,room_type,bed</code>, one line per bed. Room types must exist. If any row is wrong, nothing is imported.
      </p>
      <ProblemAlert error={upload.error} />
      {upload.data && (
        <Alert variant="success">
          Imported {upload.data.rows} rows: {Object.entries(upload.data.created).map(([k, v]) => `${v} ${k}`).join(', ')}.
        </Alert>
      )}
      <div className="flex gap-2">
        <Input type="file" accept=".csv,text/csv" aria-label="CSV file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <Button onClick={() => upload.mutate()} disabled={!file || upload.isPending}>
          Import
        </Button>
      </div>
    </div>
  )
}

function useCreate(path: string, invalidate: unknown[]) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: object) => api(path, { method: 'POST', body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: invalidate }),
  })
}

function InlineForm({ label, fields, onSubmit, error }: { label: string; fields: { name: string; label: string; type?: string }[]; onSubmit: (v: Record<string, string>) => void; error: unknown }) {
  const [values, setValues] = useState<Record<string, string>>({})
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e: FormEvent) => {
        e.preventDefault()
        onSubmit(values)
        setValues({})
      }}
    >
      {fields.map((f) => (
        <div key={f.name} className="w-32">
          <Field id={`${label}-${f.name}`} label={f.label}>
            <Input id={`${label}-${f.name}`} type={f.type ?? 'text'} value={values[f.name] ?? ''} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />
          </Field>
        </div>
      ))}
      <Button type="submit" size="sm" variant="outline">
        {label}
      </Button>
      <div className="w-full">
        <ProblemAlert error={error} />
      </div>
    </form>
  )
}

function Rooms({ floor, roomTypes, canManage }: { floor: FloorOut; roomTypes: RoomTypeOut[]; canManage: boolean }) {
  const rooms = useQuery({ queryKey: ['rooms', floor.id], queryFn: () => api<RoomOut[]>(`/admin/floors/${floor.id}/rooms`) })
  const [roomType, setRoomType] = useState('')
  const addRoom = useCreate(`/admin/floors/${floor.id}/rooms`, ['rooms', floor.id])
  return (
    <div className="ml-4 space-y-1">
      {rooms.data?.map((r) => (
        <p key={r.id} className="text-sm">
          Room {r.number} · {roomTypes.find((t) => t.id === r.room_type_id)?.name} · {r.status}
        </p>
      ))}
      {canManage && (
        <div className="flex flex-wrap items-end gap-2">
          <div className="w-40">
            <Field id={`rt-${floor.id}`} label="Room type">
              <Select id={`rt-${floor.id}`} value={roomType} onChange={(e) => setRoomType(e.target.value)}>
                <option value="">Choose…</option>
                {roomTypes.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <InlineForm label="Add room" fields={[{ name: 'number', label: 'Number' }]} error={addRoom.error} onSubmit={(v) => addRoom.mutate({ number: v.number, room_type_id: roomType })} />
        </div>
      )}
    </div>
  )
}

function Building({ building, roomTypes, canManage }: { building: BuildingOut; roomTypes: RoomTypeOut[]; canManage: boolean }) {
  const floors = useQuery({ queryKey: ['floors', building.id], queryFn: () => api<FloorOut[]>(`/admin/buildings/${building.id}/floors`) })
  const addFloor = useCreate(`/admin/buildings/${building.id}/floors`, ['floors', building.id])
  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <p className="font-medium">Building {building.name}</p>
      {floors.data?.map((f) => (
        <div key={f.id}>
          <p className="text-sm font-medium">Floor {f.level}</p>
          <Rooms floor={f} roomTypes={roomTypes} canManage={canManage} />
        </div>
      ))}
      {canManage && <InlineForm label="Add floor" fields={[{ name: 'level', label: 'Level', type: 'number' }]} error={addFloor.error} onSubmit={(v) => addFloor.mutate({ level: Number(v.level) })} />}
    </div>
  )
}

/** Leave rules (Q35). The roll-call and gate settings went with attendance and gate passes (D75); the
 *  database keeps those columns, unused. */
function HostelSettings({ hostel }: { hostel: HostelOut }) {
  const queryClient = useQueryClient()
  const [needsApproval, setNeedsApproval] = useState(hostel.local_outing_requires_approval)
  const save = useMutation({
    mutationFn: () =>
      api<HostelOut>(`/admin/hostels/${hostel.id}`, {
        method: 'PATCH',
        body: { version: hostel.version, local_outing_requires_approval: needsApproval },
      }),
    onSuccess: (h) => queryClient.setQueryData(['hostel', hostel.id], h),
  })
  return (
    <Card className="space-y-3" data-testid="hostel-settings">
      <CardTitle className="text-base">Leave</CardTitle>
      <ProblemAlert error={save.error} />
      <form
        className="flex flex-wrap items-center gap-4"
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={needsApproval} onChange={(e) => setNeedsApproval(e.target.checked)} />
          Local outings need an administrator&apos;s approval
        </label>
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={save.isPending}>
            Save hostel settings
          </Button>
          {save.isSuccess && <span className="text-sm text-success">Saved.</span>}
        </div>
      </form>
    </Card>
  )
}

export function HostelDetailPage() {
  const { hostelId = '' } = useParams()
  const me = useMe()
  const hostel = useQuery({ queryKey: ['hostel', hostelId], queryFn: () => api<HostelOut>(`/admin/hostels/${hostelId}`) })
  const buildings = useQuery({ queryKey: ['buildings', hostelId], queryFn: () => api<BuildingOut[]>(`/admin/hostels/${hostelId}/buildings`) })
  const roomTypes = useQuery({ queryKey: ['room-types', hostelId], queryFn: () => api<RoomTypeOut[]>(`/admin/hostels/${hostelId}/room-types`) })
  const addBuilding = useCreate(`/admin/hostels/${hostelId}/buildings`, ['buildings', hostelId])
  const addRoomType = useCreate(`/admin/hostels/${hostelId}/room-types`, ['room-types', hostelId])
  const canManage = !!me.data?.permissions.includes('hostels.manage')
  const canBoard = !!me.data?.permissions.includes('beds.view_board')
  if (hostel.error) return <ProblemAlert error={hostel.error} />
  return (
    <div className="max-w-5xl space-y-6">
      <PageHeader title={hostel.data ? `${hostel.data.name} (${hostel.data.code})` : 'Hostel'} />
      {canManage && hostel.data && <HostelSettings hostel={hostel.data} />}
      {canBoard && (
        <Card className="space-y-3">
          <CardTitle className="text-base">Bed board</CardTitle>
          <BedBoardView hostelId={hostelId} />
        </Card>
      )}
      <Card className="space-y-3">
        <CardTitle className="text-base">Room types</CardTitle>
        <ul className="text-sm">
          {roomTypes.data?.map((t) => (
            <li key={t.id}>
              {t.name}: {t.beds_per_room} bed(s)
            </li>
          ))}
        </ul>
        {canManage && (
          <InlineForm
            label="Add room type"
            fields={[
              { name: 'name', label: 'Name' },
              { name: 'beds_per_room', label: 'Beds per room', type: 'number' },
            ]}
            error={addRoomType.error}
            onSubmit={(v) => addRoomType.mutate({ name: v.name, beds_per_room: Number(v.beds_per_room) })}
          />
        )}
      </Card>
      <Card className="space-y-3">
        <CardTitle className="text-base">Buildings, floors and rooms</CardTitle>
        {buildings.data?.map((b) => (
          <Building key={b.id} building={b} roomTypes={roomTypes.data ?? []} canManage={canManage} />
        ))}
        {canManage && <InlineForm label="Add building" fields={[{ name: 'name', label: 'Name' }]} error={addBuilding.error} onSubmit={(v) => addBuilding.mutate({ name: v.name })} />}
      </Card>
      {canManage && (
        <Card className="space-y-3">
          <CardTitle className="text-base">Import rooms and beds (CSV)</CardTitle>
          <CsvImport hostelId={hostelId} />
        </Card>
      )}
    </div>
  )
}
