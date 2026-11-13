// The student's own profile (docs/04 §12.18, Q46): phone and emergency contact are editable,
// everything else is shown read-only. Identity documents appear only as type, masked number
// and verification status (Q62).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '@/api/client'
import type { MeOut, PortalProfile } from '@/api/types'
import { Photo } from '@/components/Photo'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PageHeader, Select } from '@/components/ui/table'

export function PortalProfilePage() {
  const queryClient = useQueryClient()
  const profile = useQuery({ queryKey: ['portal-profile'], queryFn: () => api<PortalProfile>('/portal/me/profile') })
  const [phone, setPhone] = useState('')
  const [contact, setContact] = useState({ full_name: '', relationship: 'father', phone: '' })
  const savePhone = useMutation({
    mutationFn: async () => {
      const me = await api<MeOut>('/me')
      return api('/me', { method: 'PATCH', body: { version: me.version, phone } })
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['portal-profile'] }),
  })
  const saveContact = useMutation({
    mutationFn: () => api('/portal/me/emergency-contact', { method: 'POST', body: contact }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['portal-profile'] }),
  })
  const p = profile.data
  if (profile.error) return <ProblemAlert error={profile.error} />
  if (!p) return null
  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader title="My profile" />
      {p.read_only && <Alert>Your stay has ended. Your records and receipts stay viewable for 90 days.</Alert>}
      <Card className="flex items-center gap-4">
        <Photo fileId={p.photo_file_id} alt={p.full_name} size={96} />
        <div className="text-sm">
          <p className="text-lg font-semibold" data-testid="portal-name">
            {p.full_name}
          </p>
          <p>{p.student_code}</p>
          <p>
            {p.program ?? ''} {p.year_of_study ? `· year ${p.year_of_study}` : ''}
          </p>
          <p>Phone: {p.phone ?? '—'}</p>
        </div>
      </Card>
      <Card className="space-y-2">
        <CardTitle className="text-base">Emergency contact</CardTitle>
        {p.guardians.map((g) => (
          <p key={g.id} className="text-sm">
            {g.full_name} ({g.relationship}) {g.phone} {g.is_primary && '· primary'}
          </p>
        ))}
        {!p.read_only && (
          <form
            className="grid items-end gap-2 sm:grid-cols-4"
            onSubmit={(e) => {
              e.preventDefault()
              saveContact.mutate()
            }}
          >
            <Field id="c-name" label="Name">
              <Input id="c-name" value={contact.full_name} onChange={(e) => setContact({ ...contact, full_name: e.target.value })} />
            </Field>
            <Field id="c-rel" label="Relationship">
              <Select id="c-rel" value={contact.relationship} onChange={(e) => setContact({ ...contact, relationship: e.target.value })}>
                {['father', 'mother', 'guardian', 'sibling', 'spouse', 'other'].map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </Select>
            </Field>
            <Field id="c-phone" label="Phone">
              <Input id="c-phone" value={contact.phone} onChange={(e) => setContact({ ...contact, phone: e.target.value })} />
            </Field>
            <Button type="submit" variant="outline">
              Save contact
            </Button>
          </form>
        )}
        <ProblemAlert error={saveContact.error} />
      </Card>
      {!p.read_only && (
        <Card className="space-y-2">
          <CardTitle className="text-base">My phone number</CardTitle>
          <ProblemAlert error={savePhone.error} />
          <div className="flex gap-2">
            <Input aria-label="New phone number" value={phone} onChange={(e) => setPhone(e.target.value)} />
            <Button variant="outline" onClick={() => savePhone.mutate()} disabled={!phone}>
              Save
            </Button>
          </div>
        </Card>
      )}
      <Card className="space-y-1 text-sm">
        <CardTitle className="text-base">Documents</CardTitle>
        {p.documents.length === 0 && <p className="text-muted-foreground">None on file.</p>}
        {p.documents.map((d, i) => (
          <p key={i}>
            {d.document_type === 'identity' ? `${d.id_type} ${d.id_number_masked ?? ''}` : 'Admission document'} · {d.verified ? 'verified' : 'awaiting verification'}
          </p>
        ))}
      </Card>
    </div>
  )
}
