// The bed map (12 November): Allocations as a floor plan, in the spirit of a seat-selection screen.
//
// Pick a hostel, a building and a floor (chips at the top); the floor shows a summary, a legend and
// one card per room with a tile per bed. Each bed has a colour AND an icon (and maintenance a hatch),
// so colour is never the only signal. Hovering or focusing a bed shows who is in it and since when.
// Clicking a bed opens a side panel with the actions that fit its state. Every action uses the same
// API routes as before (POST /admin/allocations, check-in, check-out, cancel, transfer, PATCH /beds),
// so the allocation rules and locks are exactly the server's: a lost race shows its message.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BedSingle, CalendarClock, Check, User, Wrench, X } from 'lucide-react'
import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'

import { api } from '@/api/client'
import type { BedBoard, HostelOut, StudentListItem } from '@/api/types'
import { useOrgTimeZone, useOrgToday } from '@/auth/session'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/table'
import { showDate, showWhen } from '@/lib/dates'
import { cn } from '@/lib/utils'

type Room = BedBoard['rooms'][number]
type Bed = Room['beds'][number]
type State = 'vacant' | 'occupied' | 'reserved' | 'maintenance' | 'inactive'

/** Colour + icon + words for each state. The words are what screen readers hear. */
export const STATES: Record<State, { label: string; tile: string; icon: ReactNode }> = {
  vacant: {
    label: 'Vacant',
    tile: 'border-emerald-600 bg-emerald-50 text-emerald-900 dark:border-emerald-400 dark:bg-emerald-950 dark:text-emerald-100',
    icon: <Check size={14} aria-hidden />,
  },
  occupied: {
    label: 'Occupied',
    tile: 'border-sky-700 bg-sky-100 text-sky-950 dark:border-sky-400 dark:bg-sky-950 dark:text-sky-100',
    icon: <User size={14} aria-hidden />,
  },
  reserved: {
    label: 'Reserved',
    tile: 'border-amber-600 bg-amber-50 text-amber-950 dark:border-amber-400 dark:bg-amber-950 dark:text-amber-100',
    icon: <CalendarClock size={14} aria-hidden />,
  },
  maintenance: {
    label: 'Maintenance',
    tile: 'border-zinc-500 text-zinc-800 dark:border-zinc-400 dark:text-zinc-100 bg-[repeating-linear-gradient(135deg,var(--muted)_0_6px,transparent_6px_12px)]',
    icon: <X size={14} aria-hidden />,
  },
  inactive: {
    label: 'Not in use',
    tile: 'border-dashed border-zinc-400 bg-transparent text-muted-foreground',
    icon: <X size={14} aria-hidden />,
  },
}

function bedLabel(room: Room, bed: Bed): string {
  return `Room ${room.number}, bed ${bed.label}`
}

/** What a bed says on hover, focus and to screen readers. */
export function bedDescription(room: Room, bed: Bed): string {
  const state = STATES[bed.state as State]?.label ?? bed.state
  const o = bed.occupant
  if (o) {
    const since = o.allocation_status === 'reserved' ? 'reserved from' : 'since'
    return `${bedLabel(room, bed)}: ${state}, ${o.student_name} (${o.student_code}) ${since} ${showDate(o.since)}`
  }
  return `${bedLabel(room, bed)}: ${state}`
}

function Chips<T extends string>({ label, options, value, onChange }: { label: string; options: { value: T; label: string }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-2">
      <span className="text-sm text-muted-foreground">{label}:</span>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
          className={cn(
            'rounded-full border px-3 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            o.value === value ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card hover:bg-muted',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

function Legend() {
  return (
    <ul aria-label="Legend" className="flex flex-wrap gap-3 text-sm" data-testid="bed-legend">
      {(['vacant', 'occupied', 'reserved', 'maintenance'] as const).map((s) => (
        <li key={s} className="flex items-center gap-1.5">
          <span className={cn('inline-flex size-6 items-center justify-center rounded border-2', STATES[s].tile)}>{STATES[s].icon}</span>
          {STATES[s].label}
        </li>
      ))}
    </ul>
  )
}

function BedTile({ room, bed, selected, onOpen }: { room: Room; bed: Bed; selected: boolean; onOpen: (bed: Bed, el: HTMLButtonElement) => void }) {
  const style = STATES[bed.state as State] ?? STATES.inactive
  const tipId = `tip-${bed.id}`
  return (
    <div className="group relative">
      <button
        type="button"
        data-testid="bed-tile"
        data-state={bed.state}
        aria-label={bedDescription(room, bed)}
        aria-describedby={tipId}
        aria-pressed={selected}
        onClick={(e) => onOpen(bed, e.currentTarget)}
        className={cn(
          'flex h-14 w-12 flex-col items-center justify-center gap-0.5 rounded-md border-2 text-xs font-semibold transition-transform hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card',
          style.tile,
          selected && 'ring-2 ring-primary ring-offset-2 ring-offset-card',
        )}
      >
        {style.icon}
        <span>{bed.label}</span>
      </button>
      <div
        id={tipId}
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-2 hidden w-56 -translate-x-1/2 rounded-md border border-border bg-card p-2 text-xs shadow-lg group-focus-within:block group-hover:block"
      >
        <p className="font-semibold">
          Bed {bed.label} · {style.label}
        </p>
        {bed.occupant ? (
          <p>
            {bed.occupant.student_name} ({bed.occupant.student_code})
            <br />
            {bed.occupant.allocation_status === 'reserved' ? 'Reserved from' : 'Since'} {showDate(bed.occupant.since)}
          </p>
        ) : (
          <p className="text-muted-foreground">{bed.state === 'vacant' ? 'Free to allocate' : 'Not available'}</p>
        )}
      </div>
    </div>
  )
}

function RoomCard({ room, selectedBed, onOpen }: { room: Room; selectedBed?: string; onOpen: (bed: Bed, el: HTMLButtonElement) => void }) {
  return (
    <Card className="space-y-2 p-3" data-testid="room-card">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="font-semibold">
          <BedSingle size={14} className="mr-1 inline" aria-hidden />
          {room.number}
        </h3>
        <span className="truncate text-xs text-muted-foreground">{room.room_type}</span>
      </div>
      {room.beds.length ? (
        <div className="flex flex-wrap gap-2">
          {room.beds.map((b) => (
            <BedTile key={b.id} room={room} bed={b} selected={b.id === selectedBed} onOpen={onOpen} />
          ))}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">No beds</p>
      )}
    </Card>
  )
}

// --- the side panel -------------------------------------------------------------------------------

function StudentPicker({ hostelId, value, onChange }: { hostelId: string; value: string; onChange: (id: string) => void }) {
  const [q, setQ] = useState('')
  const found = useQuery({
    queryKey: ['student-search', hostelId, q],
    // Only students without a bed; the server needs 2+ characters to search.
    queryFn: () =>
      api<{ items: StudentListItem[] }>(
        `/admin/students?page_size=8&status=active&without_bed=true&hostel_id=${hostelId}${q.trim().length >= 2 ? `&q=${encodeURIComponent(q.trim())}` : ''}`,
      ),
  })
  return (
    <div className="space-y-2">
      <Field id="bm-search" label="Find a student" hint="Name or Student ID: students of this hostel without a bed.">
        <Input id="bm-search" value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" />
      </Field>
      <fieldset className="max-h-56 space-y-1 overflow-y-auto" aria-label="Students">
        {found.data?.items.length === 0 && <p className="text-sm text-muted-foreground">No student without a bed matches.</p>}
        {found.data?.items.map((s) => (
          <label key={s.id} className={cn('flex cursor-pointer items-center gap-2 rounded-md border px-2 py-1.5 text-sm', value === s.id ? 'border-primary bg-muted' : 'border-border')}>
            <input type="radio" name="bm-student" value={s.id} checked={value === s.id} onChange={() => onChange(s.id)} />
            <span>
              {s.full_name} <span className="text-xs text-muted-foreground">{s.student_code}</span>
            </span>
          </label>
        ))}
      </fieldset>
    </div>
  )
}

function Panel({ hostelId, room, bed, vacantBeds, onClose }: { hostelId: string; room: Room; bed: Bed; vacantBeds: { id: string; label: string }[]; onClose: () => void }) {
  const queryClient = useQueryClient()
  const today = useOrgToday()
  const tz = useOrgTimeZone()
  const heading = useRef<HTMLHeadingElement>(null)
  const [student, setStudent] = useState('')
  const [start, setStart] = useState(today)
  const [target, setTarget] = useState('')
  const [done, setDone] = useState('')
  useEffect(() => heading.current?.focus(), [bed.id])
  const refresh = (message: string) => {
    setDone(message)
    void queryClient.invalidateQueries({ queryKey: ['bed-board', hostelId] })
    void queryClient.invalidateQueries({ queryKey: ['allocations-list'] })
  }
  const allocate = useMutation({
    mutationFn: () => api('/admin/allocations', { method: 'POST', body: { student_id: student, bed_id: bed.id, mode: 'assign', start_date: start || null } }),
    onSuccess: () => refresh('Allocated.'),
  })
  const act = useMutation({
    mutationFn: ({ action, body }: { action: string; body?: object }) => api(`/admin/allocations/${bed.occupant!.allocation_id}/${action}`, { method: 'POST', body }),
    onSuccess: (_, v) => refresh({ 'check-out': 'Checked out.', transfer: 'Transferred.', 'check-in': 'Checked in.', cancel: 'Reservation cancelled.' }[v.action] ?? 'Done.'),
  })
  const setStatus = useMutation({
    mutationFn: (status: 'available' | 'maintenance') => api(`/admin/beds/${bed.id}`, { method: 'PATCH', body: { version: bed.version, status } }),
    onSuccess: (_, s) => refresh(s === 'available' ? 'The bed is back in use.' : 'Marked under maintenance.'),
  })
  const error = allocate.error ?? act.error ?? setStatus.error
  const o = bed.occupant
  const state = STATES[bed.state as State] ?? STATES.inactive
  return (
    <aside
      aria-labelledby="bm-panel-title"
      data-testid="bed-panel"
      className="fixed inset-x-0 bottom-0 z-30 max-h-[85vh] overflow-y-auto rounded-t-xl border border-border bg-card p-4 shadow-2xl lg:sticky lg:top-4 lg:inset-auto lg:max-h-[calc(100vh-2rem)] lg:w-80 lg:shrink-0 lg:rounded-xl"
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose()
      }}
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 id="bm-panel-title" ref={heading} tabIndex={-1} className="text-lg font-semibold focus:outline-none">
            {bedLabel(room, bed)}
          </h2>
          <p className="flex items-center gap-1 text-sm text-muted-foreground">
            {state.icon} {state.label} · {room.building}, floor {room.floor_level} · {room.room_type}
          </p>
        </div>
        <Button variant="ghost" size="sm" aria-label="Close" onClick={onClose}>
          <X size={16} aria-hidden />
        </Button>
      </div>
      <div className="space-y-3">
        <ProblemAlert error={error} />
        {done && <Alert variant="success">{done}</Alert>}

        {bed.state === 'vacant' && !done && (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault()
              allocate.mutate()
            }}
          >
            <StudentPicker hostelId={hostelId} value={student} onChange={setStudent} />
            <Field id="bm-start" label="Check-in date">
              <Input id="bm-start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
            </Field>
            <Button type="submit" className="w-full" disabled={!student || allocate.isPending}>
              Allocate
            </Button>
          </form>
        )}

        {o && (
          <div className="space-y-1 text-sm" data-testid="bed-occupant">
            <p className="font-semibold">{o.student_name}</p>
            <p className="text-muted-foreground">{o.student_code}</p>
            <p>
              {o.allocation_status === 'reserved' ? 'Reserved from' : 'Staying since'} {showDate(o.since)}
            </p>
            {o.reservation_expires_at && <p>Reservation expires {showWhen(o.reservation_expires_at, tz)}</p>}
            <Link className="inline-block text-primary underline" to={`/admin/students/${o.student_id}`}>
              View student
            </Link>
          </div>
        )}

        {bed.state === 'occupied' && o && !done && (
          <div className="space-y-3">
            <Field id="bm-transfer" label="Transfer to a vacant bed">
              <Select id="bm-transfer" value={target} onChange={(e) => setTarget(e.target.value)}>
                <option value="">Choose…</option>
                {vacantBeds.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.label}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button disabled={!target || act.isPending} onClick={() => act.mutate({ action: 'transfer', body: { bed_id: target } })}>
                Transfer
              </Button>
              <Button variant="outline" disabled={act.isPending} onClick={() => act.mutate({ action: 'check-out' })}>
                Check out
              </Button>
            </div>
          </div>
        )}

        {bed.state === 'reserved' && o && !done && (
          <div className="flex flex-wrap gap-2">
            <Button disabled={act.isPending} onClick={() => act.mutate({ action: 'check-in' })}>
              Check in
            </Button>
            <Button variant="outline" disabled={act.isPending} onClick={() => act.mutate({ action: 'cancel' })}>
              Cancel reservation
            </Button>
          </div>
        )}

        {(bed.state === 'maintenance' || bed.state === 'inactive') && !done && (
          <div className="space-y-2 text-sm">
            {bed.status === 'available' ? (
              <p>The whole room is {room.status === 'inactive' ? 'not in use' : 'under maintenance'}. Change it on the hostel&apos;s page.</p>
            ) : (
              <>
                <p>This bed is {bed.status === 'inactive' ? 'not in use' : 'under maintenance'}.</p>
                <Button disabled={setStatus.isPending} onClick={() => setStatus.mutate('available')}>
                  <Wrench size={14} aria-hidden /> Back in use
                </Button>
              </>
            )}
          </div>
        )}

        {bed.state === 'vacant' && !done && (
          <Button variant="outline" size="sm" disabled={setStatus.isPending} onClick={() => setStatus.mutate('maintenance')}>
            <Wrench size={14} aria-hidden /> Mark under maintenance
          </Button>
        )}
      </div>
    </aside>
  )
}

// --- the page ---------------------------------------------------------------------------------------

export function BedMap() {
  const hostels = useQuery({ queryKey: ['hostels'], queryFn: () => api<HostelOut[]>('/admin/hostels') })
  const [hostelId, setHostelId] = useState('')
  const hostel = hostelId || hostels.data?.[0]?.id || ''
  const board = useQuery({ queryKey: ['bed-board', hostel], queryFn: () => api<BedBoard>(`/admin/hostels/${hostel}/bed-board`), enabled: !!hostel })
  const rooms = useMemo(() => board.data?.rooms ?? [], [board.data])
  const buildings = useMemo(() => [...new Map(rooms.map((r) => [r.building_id, r.building])).entries()].map(([value, label]) => ({ value, label })), [rooms])
  const [buildingId, setBuildingId] = useState('')
  const building = buildings.some((b) => b.value === buildingId) ? buildingId : buildings[0]?.value ?? ''
  const floors = useMemo(
    () =>
      [...new Map(rooms.filter((r) => r.building_id === building).map((r) => [r.floor_id, r.floor_level])).entries()]
        .sort((a, b) => a[1] - b[1])
        .map(([value, level]) => ({ value, label: `Floor ${level}` })),
    [rooms, building],
  )
  const [floorId, setFloorId] = useState('')
  const floor = floors.some((f) => f.value === floorId) ? floorId : floors[0]?.value ?? ''
  const floorRooms = rooms.filter((r) => r.floor_id === floor)
  const floorBeds = floorRooms.flatMap((r) => r.beds)
  const count = (s: State) => floorBeds.filter((b) => b.state === s).length
  const vacantBeds = rooms.flatMap((r) => r.beds.filter((b) => b.state === 'vacant').map((b) => ({ id: b.id, label: `${r.building}-${r.number} / ${b.label}` })))

  const [chosen, setChosen] = useState<string | null>(null)
  const [opened, setOpened] = useState(0) // every open is a fresh panel that takes focus, even for the same bed
  const opener = useRef<HTMLButtonElement | null>(null)
  const chosenRoom = rooms.find((r) => r.beds.some((b) => b.id === chosen))
  const chosenBed = chosenRoom?.beds.find((b) => b.id === chosen)
  const close = () => {
    setChosen(null)
    opener.current?.focus() // back where the person was
  }

  return (
    <div className="space-y-4" data-testid="bed-map">
      <ProblemAlert error={hostels.error ?? board.error} />
      <div className="space-y-2">
        {(hostels.data?.length ?? 0) > 1 && (
          <Chips
            label="Hostel"
            options={(hostels.data ?? []).map((h) => ({ value: h.id, label: h.name }))}
            value={hostel}
            onChange={(v) => {
              setHostelId(v)
              setChosen(null)
            }}
          />
        )}
        {buildings.length > 0 && <Chips label="Building" options={buildings} value={building} onChange={(v) => setBuildingId(v)} />}
        {floors.length > 0 && <Chips label="Floor" options={floors} value={floor} onChange={(v) => setFloorId(v)} />}
      </div>

      {floor && (
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-5" data-testid="floor-summary" aria-label="This floor">
          {[
            ['Total', floorBeds.length],
            ['Occupied', count('occupied')],
            ['Vacant', count('vacant')],
            ['Reserved', count('reserved')],
            ['Maintenance', count('maintenance')],
          ].map(([label, n]) => (
            <div key={label} className="rounded-lg border border-border bg-card px-3 py-2">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="text-xl font-semibold">{n}</dd>
            </div>
          ))}
        </dl>
      )}
      <Legend />

      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        <div className="grid flex-1 grid-cols-[repeat(auto-fill,minmax(9.5rem,1fr))] gap-3" data-testid="floor-plan">
          {board.isPending && hostel && <p className="text-sm text-muted-foreground">Loading the floor…</p>}
          {board.isSuccess && floorRooms.length === 0 && <p className="text-sm text-muted-foreground">No rooms on this floor yet. Add them on the hostel&apos;s page.</p>}
          {floorRooms.map((r) => (
            <RoomCard
              key={r.id}
              room={r}
              selectedBed={chosen ?? undefined}
              onOpen={(b, el) => {
                opener.current = el
                setChosen(b.id)
                setOpened((n) => n + 1)
              }}
            />
          ))}
        </div>
        {chosenRoom && chosenBed && <Panel key={`${chosenBed.id}-${opened}`} hostelId={hostel} room={chosenRoom} bed={chosenBed} vacantBeds={vacantBeds.filter((b) => b.id !== chosenBed.id)} onClose={close} />}
      </div>
    </div>
  )
}
