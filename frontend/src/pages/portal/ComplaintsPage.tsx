// The student's complaints (docs/04 §12.12; Q40): raise one with up to 3 photos, follow its
// history, and reopen it within 7 days of resolution.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api, apiUpload } from '@/api/client'
import type { CategoryOut, ComplaintDetail, UploadOut } from '@/api/types'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PageHeader, Select } from '@/components/ui/table'
import { showDate, showDay } from '@/lib/dates'
import { statusLabel } from '@/pages/admin/Complaints'
import { useOrgTimeZone } from '@/auth/session'

export function PortalComplaintsPage() {
  const tz = useOrgTimeZone()
  const queryClient = useQueryClient()
  const cats = useQuery({ queryKey: ['my-complaint-categories'], queryFn: () => api<CategoryOut[]>('/portal/me/complaint-categories') })
  const list = useQuery({ queryKey: ['my-complaints'], queryFn: () => api<ComplaintDetail[]>('/portal/me/complaints') })
  const [form, setForm] = useState({ category_id: '', title: '', description: '', priority: 'medium' })
  const [files, setFiles] = useState<File[]>([])
  const category = form.category_id || cats.data?.[0]?.id || ''
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['my-complaints'] })
  const create = useMutation({
    mutationFn: async () => {
      const ids: string[] = []
      for (const f of files.slice(0, 3)) {
        const fd = new FormData()
        fd.append('category', 'complaint_attachment')
        fd.append('file', f)
        ids.push((await apiUpload<UploadOut>('/files', fd)).file_id)
      }
      return api<ComplaintDetail>('/portal/me/complaints', { method: 'POST', body: { ...form, category_id: category, attachment_file_ids: ids } })
    },
    onSuccess: () => {
      setForm({ ...form, title: '', description: '' })
      setFiles([])
      refresh()
    },
  })
  const [why, setWhy] = useState<Record<string, string>>({})
  const reopen = useMutation({
    mutationFn: (id: string) => api(`/portal/me/complaints/${id}/reopen`, { method: 'POST', body: { comment: why[id] } }),
    onSuccess: refresh,
  })
  return (
    <div className="max-w-3xl space-y-4">
      <PageHeader title="My complaints" />
      <Card className="space-y-3">
        <CardTitle className="text-base">Raise a complaint</CardTitle>
        <ProblemAlert error={create.error ?? cats.error ?? list.error ?? reopen.error} />
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate()
          }}
        >
          <Field id="c-cat" label="Category">
            <Select id="c-cat" value={category} onChange={(e) => setForm({ ...form, category_id: e.target.value })}>
              {(cats.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field id="c-priority" label="Priority">
            <Select id="c-priority" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="urgent">Urgent</option>
            </Select>
          </Field>
          <div className="sm:col-span-2">
            <Field id="c-title" label="Title">
              <Input id="c-title" required minLength={3} maxLength={120} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </Field>
          </div>
          <div className="sm:col-span-2">
            <Field id="c-desc" label="What's wrong?">
              <Input id="c-desc" required minLength={3} maxLength={4000} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
          </div>
          <Field id="c-photos" label="Photos (up to 3, optional)">
            <Input id="c-photos" type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
          </Field>
          <div className="self-end">
            <Button type="submit" disabled={!category || create.isPending}>
              Submit
            </Button>
          </div>
        </form>
      </Card>
      {list.isSuccess && list.data.length === 0 && <p className="text-sm text-muted-foreground">You haven't raised any complaints. They will appear here with their progress.</p>}
      <div className="space-y-2" data-testid="my-complaints">
        {(list.data ?? []).map((c) => (
          <Card key={c.id} className="space-y-1 text-sm">
            <p className="font-semibold">
              {c.title} · {statusLabel(c.status)}
            </p>
            <p className="text-xs text-muted-foreground">
              {c.category} · {c.priority} · target {showDate(c.target_date)}
            </p>
            {c.resolution_note && <p>Resolution: {c.resolution_note}</p>}
            {c.can_reopen_until && (
              <div className="flex gap-2 pt-1">
                <Input aria-label="Why reopen?" placeholder="Why reopen?" className="w-64" value={why[c.id] ?? ''} onChange={(e) => setWhy({ ...why, [c.id]: e.target.value })} />
                <Button size="sm" variant="outline" disabled={(why[c.id] ?? '').trim().length < 3} onClick={() => reopen.mutate(c.id)}>
                  Reopen
                </Button>
                <span className="self-center text-xs text-muted-foreground">until {showDay(c.can_reopen_until, tz)}</span>
              </div>
            )}
          </Card>
        ))}
      </div>
    </div>
  )
}
