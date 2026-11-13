// The Platform Owner console (docs/04 §12.19; Q14, Q15, Q50, Q55): organizations with counts
// only, suspend/reactivate, health, the masked
// audit log and global settings. Every call uses the platform realm (its own token and keys).
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '@/api/client'
import type { PlatformAuditPage, PlatformHealth, PlatformOrgDetail, PlatformOrgPage, SettingOut } from '@/api/types'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { PageHeader, Select, Table, Td, Th } from '@/components/ui/table'
import { showDay, showWhen } from '@/lib/dates'

const P = { realm: 'platform' as const }

function OrgDetail({ id }: { id: string }) {
  const queryClient = useQueryClient()
  const org = useQuery({ queryKey: ['platform-org', id], queryFn: () => api<PlatformOrgDetail>(`/platform/organizations/${id}`, P) })
  const [reason, setReason] = useState('')
  const act = useMutation({
    mutationFn: (action: 'suspend' | 'reactivate') => api(`/platform/organizations/${id}/${action}`, { ...P, method: 'POST', body: { reason } }),
    onSuccess: () => {
      setReason('')
      void queryClient.invalidateQueries({ queryKey: ['platform-org', id] })
      void queryClient.invalidateQueries({ queryKey: ['platform-orgs'] })
    },
  })
  const o = org.data
  if (!o) return <ProblemAlert error={org.error} />
  return (
    <Card className="space-y-3" data-testid="platform-org">
      <CardTitle className="text-base">
        {o.name} · {o.status.replace('_', ' ')}
      </CardTitle>
      <ProblemAlert error={act.error} />
      <p className="text-sm">
        {o.legal_name ?? '—'} · {o.city ?? '—'} {o.state ?? ''} · {o.contact_email ?? '—'} · timezone {o.timezone}
      </p>
      <p className="text-sm">
        {o.counts.hostels} hostels · {o.counts.beds} beds · {o.counts.students_active} active students · {o.counts.staff_active} active staff · {o.logins_last_7_days} sign-ins in 7 days
      </p>
      {o.suspension_reason && <p className="text-sm text-destructive">Suspended: {o.suspension_reason}</p>}
      <div className="flex flex-wrap gap-2">
        <Input aria-label="Reason" placeholder="Reason (at least 10 characters)" className="w-80" value={reason} onChange={(e) => setReason(e.target.value)} />
        {o.status === 'active' && (
          <Button variant="outline" disabled={reason.trim().length < 10} onClick={() => act.mutate('suspend')}>
            Suspend
          </Button>
        )}
        {o.status === 'suspended' && (
          <Button variant="outline" disabled={reason.trim().length < 10} onClick={() => act.mutate('reactivate')}>
            Reactivate
          </Button>
        )}
      </div>
    </Card>
  )
}

export function PlatformOrganizationsPage() {
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const orgs = useQuery({
    queryKey: ['platform-orgs', q, status],
    queryFn: () => api<PlatformOrgPage>(`/platform/organizations?page_size=100${q ? `&q=${encodeURIComponent(q)}` : ''}${status ? `&status=${status}` : ''}`, P),
  })
  const [chosen, setChosen] = useState<string | null>(null)
  return (
    <div className="space-y-4">
      <PageHeader
        title="Organizations"
        actions={
          <div className="flex gap-2">
            <Input aria-label="Search" placeholder="Name or address" value={q} onChange={(e) => setQ(e.target.value)} />
            <Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All</option>
              <option value="active">Active</option>
              <option value="suspended">Suspended</option>
              <option value="pending_verification">Pending verification</option>
            </Select>
          </div>
        }
      />
      <ProblemAlert error={orgs.error} />
      <Table data-testid="platform-orgs">
        <thead>
          <tr>
            <Th>Organization</Th>
            <Th>Status</Th>
            <Th>Hostels</Th>
            <Th>Students</Th>
            <Th>Created</Th>
          </tr>
        </thead>
        <tbody>
          {(orgs.data?.items ?? []).map((o) => (
            <tr key={o.id} className="cursor-pointer" onClick={() => setChosen(o.id)}>
              <Td className="underline">
                {o.name} <span className="text-xs text-muted-foreground">{o.slug}</span>
              </Td>
              <Td>{o.status.replace('_', ' ')}</Td>
              <Td>{o.counts.hostels}</Td>
              <Td>{o.counts.students_active}</Td>
              <Td>{showDay(o.created_at)}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {chosen && <OrgDetail key={chosen} id={chosen} />}
    </div>
  )
}

const tone = { ok: 'text-success', warning: 'text-amber-700 dark:text-amber-300', error: 'text-destructive', unknown: 'text-muted-foreground' }

export function PlatformHealthPage() {
  const h = useQuery({ queryKey: ['platform-health'], queryFn: () => api<PlatformHealth>('/platform/health', P), refetchInterval: 60_000 })
  return (
    <div className="max-w-3xl space-y-4">
      <PageHeader title="Health" />
      <ProblemAlert error={h.error} />
      <Table data-testid="platform-health">
        <tbody>
          {(h.data?.items ?? []).map((i) => (
            <tr key={i.name}>
              <Td className="font-medium">{i.name}</Td>
              <Td className={tone[i.status]}>{i.status}</Td>
              <Td>{i.detail}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {h.data && <p className="text-xs text-muted-foreground">Checked {showWhen(h.data.checked_at)}</p>}
    </div>
  )
}

export function PlatformAuditPage() {
  const [action, setAction] = useState('')
  const logs = useInfiniteQuery({
    queryKey: ['platform-audit', action],
    initialPageParam: '',
    queryFn: ({ pageParam }) => api<PlatformAuditPage>(`/platform/audit-logs?limit=50${pageParam ? `&cursor=${pageParam}` : ''}${action ? `&action=${encodeURIComponent(action)}` : ''}`, P),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
  return (
    <div className="space-y-4">
      <PageHeader title="Audit log (masked)" actions={<Input aria-label="Action" placeholder="Action" value={action} onChange={(e) => setAction(e.target.value.trim())} />} />
      <p className="text-sm text-muted-foreground">Actions and record IDs only; changed values, IP addresses and browsers are not visible to the platform (Q55).</p>
      <ProblemAlert error={logs.error} />
      <Table data-testid="platform-audit">
        <thead>
          <tr>
            <Th>When</Th>
            <Th>Organization</Th>
            <Th>Action</Th>
            <Th>Changed fields</Th>
          </tr>
        </thead>
        <tbody>
          {(logs.data?.pages.flatMap((p) => p.items) ?? []).map((r) => (
            <tr key={r.id}>
              <Td>{showWhen(r.occurred_at)}</Td>
              <Td className="font-mono text-xs">{r.org_id ?? 'platform'}</Td>
              <Td>
                {r.action} <span className="text-xs text-muted-foreground">{r.actor_type}</span>
              </Td>
              <Td className="text-xs">{r.changed_fields.join(', ')}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {logs.hasNextPage && (
        <Button variant="outline" onClick={() => void logs.fetchNextPage()}>
          Load more
        </Button>
      )}
    </div>
  )
}

export function PlatformSettingsPage() {
  const queryClient = useQueryClient()
  const s = useQuery({ queryKey: ['platform-settings'], queryFn: () => api<SettingOut[]>('/platform/settings', P) })
  const [values, setValues] = useState<Record<string, string>>({})
  const save = useMutation({
    mutationFn: (row: SettingOut) => api('/platform/settings', { ...P, method: 'PATCH', body: { key: row.key, value: Number(values[row.key]), version: row.version } }),
    onSuccess: () => {
      setValues({})
      void queryClient.invalidateQueries({ queryKey: ['platform-settings'] })
    },
  })
  return (
    <div className="max-w-3xl space-y-4">
      <PageHeader title="Settings" />
      <ProblemAlert error={s.error ?? save.error} />
      <Table data-testid="platform-settings">
        <tbody>
          {(s.data ?? []).map((row) => (
            <tr key={row.key}>
              <Td>
                {row.description}
                <span className="block font-mono text-xs text-muted-foreground">{row.key}</span>
              </Td>
              <Td>
                <Input aria-label={row.key} className="w-24" value={values[row.key] ?? String(row.value)} onChange={(e) => setValues({ ...values, [row.key]: e.target.value })} />
              </Td>
              <Td>
                <Button size="sm" variant="outline" disabled={values[row.key] === undefined} onClick={() => save.mutate(row)}>
                  Save
                </Button>
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  )
}
