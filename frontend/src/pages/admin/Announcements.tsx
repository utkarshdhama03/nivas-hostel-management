// Announcements (docs/04 §12.15): Administrators post to one hostel or to everyone;
// each person in the audience also gets a notification. Plus the student's list.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '@/api/client'
import type { AnnouncementOut, HostelOut } from '@/api/types'
import { useMe, useOrgTimeZone } from '@/auth/session'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PageHeader, Select } from '@/components/ui/table'
import { showDay, showWhen } from '@/lib/dates'

function List({ items, hostels }: { items: AnnouncementOut[]; hostels?: HostelOut[] }) {
  const tz = useOrgTimeZone()
  const name = (id: string | null) => (id ? hostels?.find((h) => h.id === id)?.name ?? 'Hostel' : 'Everyone')
  return (
    <div className="space-y-2" data-testid="announcements">
      {items.map((a) => (
        <Card key={a.id} className="space-y-1">
          <p className="font-semibold">{a.title}</p>
          <p className="whitespace-pre-line text-sm">{a.body}</p>
          <p className="text-xs text-muted-foreground">
            {showWhen(a.published_at, tz)}
            {hostels && ` · ${name(a.hostel_id)} · ${a.audience}`}
            {a.expires_at && ` · until ${showDay(a.expires_at, tz)}`}
          </p>
        </Card>
      ))}
    </div>
  )
}

export function AnnouncementsPage() {
  const queryClient = useQueryClient()
  const isSuperAdmin = !!useMe().data?.roles?.some((r) => r.role === 'super_admin')
  const hostels = useQuery({ queryKey: ['hostels'], queryFn: () => api<HostelOut[]>('/admin/hostels') })
  const list = useQuery({ queryKey: ['announcements'], queryFn: () => api<AnnouncementOut[]>('/admin/announcements') })
  const [form, setForm] = useState({ hostel_id: '', title: '', body: '', audience: 'everyone' })
  const hostelId = form.hostel_id || (isSuperAdmin ? '' : hostels.data?.[0]?.id ?? '')
  const post = useMutation({
    mutationFn: () => api('/admin/announcements', { method: 'POST', body: { ...form, hostel_id: hostelId || null } }),
    onSuccess: () => {
      setForm({ ...form, title: '', body: '' })
      void queryClient.invalidateQueries({ queryKey: ['announcements'] })
    },
  })
  return (
    <div className="max-w-3xl space-y-4">
      <PageHeader title="Announcements" />
      <Card className="space-y-3">
        <CardTitle className="text-base">Post an announcement</CardTitle>
        <ProblemAlert error={post.error ?? list.error} />
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault()
            post.mutate()
          }}
        >
          <Field id="an-hostel" label="For">
            <Select id="an-hostel" value={hostelId} onChange={(e) => setForm({ ...form, hostel_id: e.target.value })}>
              {isSuperAdmin && <option value="">The whole organization</option>}
              {(hostels.data ?? []).map((h) => (
                <option key={h.id} value={h.id}>
                  {h.code} · {h.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field id="an-audience" label="Audience">
            <Select id="an-audience" value={form.audience} onChange={(e) => setForm({ ...form, audience: e.target.value })}>
              <option value="everyone">Everyone</option>
              <option value="students">Students</option>
              <option value="staff">Staff</option>
            </Select>
          </Field>
          <div className="sm:col-span-2">
            <Field id="an-title" label="Title">
              <Input id="an-title" required minLength={3} maxLength={120} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </Field>
          </div>
          <div className="sm:col-span-2">
            <Field id="an-body" label="Message">
              <Input id="an-body" required maxLength={4000} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} />
            </Field>
          </div>
          <div>
            <Button type="submit" disabled={post.isPending}>
              Post
            </Button>
          </div>
        </form>
      </Card>
      <List items={list.data ?? []} hostels={hostels.data} />
    </div>
  )
}

export function PortalAnnouncementsPage() {
  const list = useQuery({ queryKey: ['my-announcements'], queryFn: () => api<AnnouncementOut[]>('/portal/me/announcements') })
  return (
    <div className="max-w-3xl space-y-4">
      <PageHeader title="Announcements" />
      <ProblemAlert error={list.error} />
      {list.isSuccess && list.data.length === 0 && <p className="text-sm text-muted-foreground">Nothing new.</p>}
      <List items={list.data ?? []} />
    </div>
  )
}
