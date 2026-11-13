// The bed map (12 November): chips, summary and legend; each bed says its state in words and icon,
// not colour alone; clicking a bed opens the panel that fits it; Escape closes it and gives focus back.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { setAccessToken } from '@/api/client'

import { BedMap } from './BedMap'

const occupant = (name: string, code: string, status: 'active' | 'reserved') => ({
  allocation_id: `al-${code}`,
  allocation_status: status,
  student_id: `st-${code}`,
  student_code: code,
  student_name: name,
  since: '2026-08-01',
  reservation_expires_at: null,
})
const bed = (id: string, label: string, state: string, extra: object = {}) => ({ id, label, state, status: state === 'maintenance' ? 'maintenance' : 'available', version: 1, occupant: null, ...extra })
const room = (id: string, number: string, floor: string, level: number, beds: object[]) => ({
  id, number, status: 'available', room_type: 'Double', building_id: 'bA', building: 'A', floor_id: floor, floor_level: level, beds,
})
const BOARD = {
  hostel_id: 'h1',
  counts: {},
  generated_at: '2026-11-12T10:00:00Z',
  rooms: [
    room('r101', '101', 'f1', 1, [bed('b1', 'A', 'occupied', { occupant: occupant('Asha Rao', 'STU-2026-0001', 'active') }), bed('b2', 'B', 'vacant')]),
    room('r102', '102', 'f1', 1, [bed('b3', 'A', 'reserved', { occupant: occupant('Ravi Kumar', 'STU-2026-0002', 'reserved') }), bed('b4', 'B', 'maintenance')]),
    room('r201', '201', 'f2', 2, [bed('b5', 'A', 'vacant')]),
  ],
}

type Call = { url: string; method: string; body?: unknown }
let calls: Call[] = []
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  calls = []
  setAccessToken('org', 'test-token')
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (url.endsWith('/me')) return json({ organization: { timezone: 'Asia/Kolkata' }, permissions: [] })
      if (url.endsWith('/admin/hostels')) return json([{ id: 'h1', name: 'Aryabhata Boys Hostel' }, { id: 'h2', name: 'Gargi Girls Hostel' }])
      if (url.includes('/bed-board')) return json(BOARD)
      if (url.includes('/admin/students')) return json({ items: [{ id: 'st-9', full_name: 'Kabir Shah', student_code: 'STU-2026-0009' }] })
      return json({})
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  setAccessToken('org', null)
})

function renderMap() {
  const router = createMemoryRouter([{ path: '/admin/allocations', element: <BedMap /> }, { path: '/admin/students/:id', element: <p>Student page</p> }], {
    initialEntries: ['/admin/allocations'],
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

test('hostel, building and floor chips; the floor summary and the legend', async () => {
  renderMap()
  expect(await screen.findByRole('button', { name: 'Aryabhata Boys Hostel', pressed: true })).toBeDefined()
  expect(await screen.findByRole('button', { name: 'Floor 1', pressed: true })).toBeDefined()
  const summary = await screen.findByTestId('floor-summary')
  const value = (label: string) => within(summary).getByText(label).nextSibling?.textContent
  expect([value('Total'), value('Occupied'), value('Vacant'), value('Reserved'), value('Maintenance')]).toEqual(['4', '1', '1', '1', '1'])
  expect(within(screen.getByTestId('bed-legend')).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Vacant', 'Occupied', 'Reserved', 'Maintenance'])
  fireEvent.click(screen.getByRole('button', { name: 'Floor 2' }))
  expect(await screen.findAllByTestId('room-card')).toHaveLength(1)
})

test('each bed says its state and occupant in words, not colour alone', async () => {
  renderMap()
  const tiles = await screen.findAllByTestId('bed-tile')
  expect(tiles.map((t) => t.getAttribute('aria-label'))).toEqual([
    'Room 101, bed A: Occupied, Asha Rao (STU-2026-0001) since 01-08-2026',
    'Room 101, bed B: Vacant',
    'Room 102, bed A: Reserved, Ravi Kumar (STU-2026-0002) reserved from 01-08-2026',
    'Room 102, bed B: Maintenance',
  ])
  for (const t of tiles) expect(t.querySelector('svg')).not.toBeNull() // an icon on every tile
  expect(screen.getAllByRole('tooltip', { hidden: true })[0]!.textContent).toContain('Asha Rao (STU-2026-0001)')
})

test('a vacant bed: pick a student, set the check-in date, allocate', async () => {
  renderMap()
  fireEvent.click(await screen.findByRole('button', { name: 'Room 101, bed B: Vacant' }))
  const panel = await screen.findByTestId('bed-panel')
  fireEvent.click(await within(panel).findByLabelText(/Kabir Shah/))
  fireEvent.change(within(panel).getByLabelText('Check-in date'), { target: { value: '2026-11-12' } })
  fireEvent.click(within(panel).getByRole('button', { name: 'Allocate' }))
  await waitFor(() => expect(calls).toContainEqual({ url: '/api/v1/admin/allocations', method: 'POST', body: { student_id: 'st-9', bed_id: 'b2', mode: 'assign', start_date: '2026-11-12' } }))
  expect(calls.some((c) => c.url.includes('without_bed=true'))).toBe(true) // only students without a bed are offered
})

test('an occupied bed: the student, Transfer, Check out and View student; Escape closes and gives focus back', async () => {
  renderMap()
  const tile = await screen.findByRole('button', { name: /Room 101, bed A: Occupied/ })
  fireEvent.click(tile)
  const panel = await screen.findByTestId('bed-panel')
  expect(within(panel).getByText('Asha Rao')).toBeDefined()
  expect(within(panel).getByRole('link', { name: 'View student' }).getAttribute('href')).toBe('/admin/students/st-STU-2026-0001')
  within(panel).getByRole('button', { name: 'Transfer' })
  fireEvent.click(within(panel).getByRole('button', { name: 'Check out' }))
  await waitFor(() => expect(calls).toContainEqual({ url: '/api/v1/admin/allocations/al-STU-2026-0001/check-out', method: 'POST', body: undefined }))
  fireEvent.keyDown(panel, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByTestId('bed-panel')).toBeNull())
  expect(document.activeElement).toBe(tile)
})

test('a reserved bed checks in or cancels; a maintenance bed goes back in use', async () => {
  renderMap()
  fireEvent.click(await screen.findByRole('button', { name: /Room 102, bed A: Reserved/ }))
  let panel = await screen.findByTestId('bed-panel')
  within(panel).getByRole('button', { name: 'Cancel reservation' })
  fireEvent.click(within(panel).getByRole('button', { name: 'Check in' }))
  await waitFor(() => expect(calls.some((c) => c.url.endsWith('/al-STU-2026-0002/check-in'))).toBe(true))
  fireEvent.click(screen.getByRole('button', { name: 'Room 102, bed B: Maintenance' }))
  panel = await screen.findByTestId('bed-panel')
  fireEvent.click(within(panel).getByRole('button', { name: 'Back in use' }))
  await waitFor(() => expect(calls).toContainEqual({ url: '/api/v1/admin/beds/b4', method: 'PATCH', body: { version: 1, status: 'available' } }))
})
