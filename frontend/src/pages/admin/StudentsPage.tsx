// Students (docs/04 §12.7): list, admission (photo first, Q39), details with role-specific
// fields, documents with the masked-Aadhaar rules (Q62), the activation email sent again (D80), exit (Q22).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { ApiError, api, apiBlob, apiUpload } from '@/api/client'
import type { DocumentOut, HostelOut, StudentFullOut, StudentListItem, UploadOut } from '@/api/types'
import { useMe, useOrgToday } from '@/auth/session'
import { Photo } from '@/components/Photo'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PageHeader, Select, Table, Td, Th } from '@/components/ui/table'
import { showDate } from '@/lib/dates'
import { StudentAllocation } from '@/pages/admin/Allocations'
import { rupees, StudentFinance } from '@/pages/admin/Finance'

async function uploadFile(category: string, file: File): Promise<UploadOut> {
  const form = new FormData()
  form.append('category', category)
  form.append('file', file)
  return apiUpload<UploadOut>('/files', form)
}

export function StudentsPage() {
  const me = useMe()
  const [q, setQ] = useState('')
  const students = useQuery({
    queryKey: ['students', q],
    queryFn: () => api<{ items: StudentListItem[] }>(`/admin/students?page_size=100${q.length >= 2 ? `&q=${encodeURIComponent(q)}` : ''}`),
  })
  const canAdmit = me.data?.permissions.includes('students.manage')
  return (
    <div className="max-w-5xl space-y-6">
      <PageHeader
        title="Students"
        actions={
          <div className="flex gap-2">
            <Input aria-label="Search students" placeholder="Name or Student ID" className="w-56" value={q} onChange={(e) => setQ(e.target.value)} />
            {canAdmit && (
              <Button asChild>
                <Link to="/admin/students/new">Admit student</Link>
              </Button>
            )}
          </div>
        }
      />
      <ProblemAlert error={students.error} />
      <Table>
        <thead>
          <tr>
            <Th>Photo</Th>
            <Th>Student ID</Th>
            <Th>Name</Th>
            <Th>Status</Th>
          </tr>
        </thead>
        <tbody>
          {students.data?.items.map((s) => (
            <tr key={s.id}>
              <Td>
                <Photo fileId={s.photo_file_id} alt={s.full_name} size={40} />
              </Td>
              <Td>{s.student_code}</Td>
              <Td>
                <Link className="text-primary underline" to={`/admin/students/${s.id}`}>
                  {s.full_name}
                </Link>
              </Td>
              <Td>{s.status}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  )
}

export function AdmitStudentPage() {
  const today = useOrgToday()
  const navigate = useNavigate()
  const hostels = useQuery({ queryKey: ['hostels'], queryFn: () => api<HostelOut[]>('/admin/hostels') })
  const [photo, setPhoto] = useState<File | null>(null)
  const [f, setF] = useState<Record<string, string>>({ gender: 'male', admission_date: today, relationship: 'father' })
  const set = (k: string) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value })
  const admit = useMutation({
    mutationFn: async () => {
      const uploaded = await uploadFile('student_photo', photo as File)
      return api<StudentFullOut>('/admin/students', {
        method: 'POST',
        body: {
          full_name: f.full_name,
          email: f.email,
          phone: f.phone || null,
          hostel_id: f.hostel_id,
          gender: f.gender,
          date_of_birth: f.date_of_birth,
          admission_date: f.admission_date,
          photo_file_id: uploaded.file_id,
          program: f.program || null,
          guardians: f.guardian_name ? [{ full_name: f.guardian_name, relationship: f.relationship, phone: f.guardian_phone, is_primary: true }] : [],
          send_activation_email: true,
        },
      })
    },
    onSuccess: (s) => navigate(`/admin/students/${s.id}`),
  })
  const field = (k: string, label: string, type = 'text') => (
    <Field id={`a-${k}`} label={label}>
      <Input id={`a-${k}`} type={type} value={f[k] ?? ''} onChange={set(k)} />
    </Field>
  )
  return (
    <div className="max-w-3xl space-y-4">
      <PageHeader title="Admit a student" />
      <Card>
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault()
            admit.mutate()
          }}
        >
          <div className="sm:col-span-2">
            <ProblemAlert error={admit.error} />
          </div>
          {field('full_name', 'Full name')}
          <Field id="a-hostel" label="Hostel">
            <Select id="a-hostel" value={f.hostel_id ?? ''} onChange={set('hostel_id')}>
              <option value="">Choose…</option>
              {hostels.data?.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.name} ({h.resident_type ?? 'any'})
                </option>
              ))}
            </Select>
          </Field>
          <Field id="a-gender" label="Gender">
            <Select id="a-gender" value={f.gender} onChange={set('gender')}>
              <option value="male">Male</option>
              <option value="female">Female</option>
              <option value="other">Other</option>
            </Select>
          </Field>
          {field('date_of_birth', 'Date of birth', 'date')}
          {field('admission_date', 'Admission date', 'date')}
          {field('email', 'Email (required: the student signs in with it; the activation link is emailed)', 'email')}
          {field('phone', 'Phone')}
          {field('program', 'Programme')}
          <Field id="a-photo" label="Photo (required, JPEG/PNG/WebP, max 5 MB)">
            <Input id="a-photo" type="file" accept="image/jpeg,image/png,image/webp" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
          </Field>
          {field('guardian_name', 'Emergency contact name')}
          {field('guardian_phone', 'Emergency contact phone')}
          <Field id="a-rel" label="Relationship">
            <Select id="a-rel" value={f.relationship} onChange={set('relationship')}>
              {['father', 'mother', 'guardian', 'sibling', 'spouse', 'other'].map((r) => (
                <option key={r}>{r}</option>
              ))}
            </Select>
          </Field>
          <div className="sm:col-span-2">
            <Button type="submit" disabled={admit.isPending || !photo || !f.full_name || !f.hostel_id || !f.date_of_birth}>
              Admit
            </Button>
          </div>
        </form>
      </Card>
    </div>
  )
}

function Documents({ student }: { student: StudentFullOut }) {
  const queryClient = useQueryClient()
  const docs = useQuery({ queryKey: ['documents', student.id], queryFn: () => api<DocumentOut[]>(`/admin/students/${student.id}/documents`) })
  const [file, setFile] = useState<File | null>(null)
  const [kind, setKind] = useState('aadhaar')
  const [masked, setMasked] = useState('')
  const [attested, setAttested] = useState(false)
  const identity = kind !== 'admission'
  const add = useMutation({
    mutationFn: async () => {
      const uploaded = await uploadFile(identity ? 'identity_document' : 'admission_document', file as File)
      return api(`/admin/students/${student.id}/documents`, {
        method: 'POST',
        body: identity
          ? { file_id: uploaded.file_id, document_type: 'identity', id_type: kind, id_number_masked: masked || null, masked_aadhaar_attested: attested }
          : { file_id: uploaded.file_id, document_type: 'admission' },
      })
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['documents', student.id] }),
  })
  const open = useMutation({
    mutationFn: async (d: DocumentOut) => {
      const blob = await apiBlob(`/admin/students/${student.id}/documents/${d.id}/content`)
      window.open(URL.createObjectURL(blob), '_blank', 'noopener')
    },
  })
  return (
    <Card className="space-y-3">
      <CardTitle className="text-base">Documents</CardTitle>
      <ProblemAlert error={docs.error ?? add.error ?? open.error} />
      <ul className="space-y-1 text-sm">
        {docs.data?.map((d) => (
          <li key={d.id} className="flex items-center gap-2">
            {d.document_type === 'identity' ? `${d.id_type} ${d.id_number_masked ?? ''}` : 'Admission document'} · {d.verified ? 'verified' : 'not verified'}
            <Button size="sm" variant="ghost" onClick={() => open.mutate(d)}>
              Open
            </Button>
          </li>
        ))}
      </ul>
      <div className="grid gap-2 sm:grid-cols-2">
        <Field id="d-kind" label="Document">
          <Select id="d-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="aadhaar">Aadhaar (masked PDF from UIDAI)</option>
            <option value="pan">PAN</option>
            <option value="passport">Passport</option>
            <option value="college_id">College ID</option>
            <option value="admission">Admission document (not an ID)</option>
          </Select>
        </Field>
        <Field id="d-file" label="File">
          <Input id="d-file" type="file" accept={kind === 'aadhaar' ? 'application/pdf' : 'application/pdf,image/*'} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Field>
        {identity && (
          <Field id="d-masked" label="Last 4 characters, masked (e.g. XXXXXXXX1234)">
            <Input id="d-masked" value={masked} onChange={(e) => setMasked(e.target.value.toUpperCase())} />
          </Field>
        )}
      </div>
      {kind === 'aadhaar' && (
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={attested} onChange={(e) => setAttested(e.target.checked)} />
          <span>
            I confirm this is the <strong>masked Aadhaar PDF downloaded from UIDAI</strong>, not a scan or photo of the card. Only the last 4 digits are visible in it.
          </span>
        </label>
      )}
      {kind === 'admission' && <Alert>Never upload an ID card as an admission document: ID cards must use the restricted identity types above.</Alert>}
      <Button variant="outline" onClick={() => add.mutate()} disabled={!file || add.isPending}>
        Upload document
      </Button>
    </Card>
  )
}

const EXIT_REASONS = [
  ['graduated', 'Graduated'],
  ['withdrawn', 'Withdrew'],
  ['expelled', 'Expelled'],
  ['transferred_out', 'Transferred out'],
  ['other', 'Other'],
] as const

export function StudentDetailPage() {
  const { studentId = '' } = useParams()
  const me = useMe()
  const queryClient = useQueryClient()
  const student = useQuery({ queryKey: ['student', studentId], queryFn: () => api<StudentFullOut>(`/admin/students/${studentId}`) })
  const [exit, setExit] = useState({ exited_on: '', exit_reason: 'graduated', outstanding_note: '' })
  // D80: no copied links; the activation email can be sent again.
  const activation = useMutation({ mutationFn: () => api(`/admin/users/${student.data?.user_id}/activation-email`, { method: 'POST' }) })
  const doExit = useMutation({
    mutationFn: () =>
      api(`/admin/students/${studentId}/exit`, {
        method: 'POST',
        body: { exited_on: exit.exited_on, exit_reason: exit.exit_reason, ...(exit.outstanding_note ? { outstanding_note: exit.outstanding_note } : {}) },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['student', studentId] }),
  })
  const canManage = !!me.data?.permissions.includes('students.manage')
  // Q93: money never blocks an exit, but the server first answers with the amounts outstanding.
  const owed = doExit.error instanceof ApiError && doExit.error.code === 'outstanding_balance' ? doExit.error.problem : null
  const s = student.data
  if (student.error) return <ProblemAlert error={student.error} />
  if (!s) return null
  return (
    <div className="max-w-4xl space-y-6">
      <div className="flex items-center gap-4">
        <Photo fileId={s.photo_file_id} alt={s.full_name} size={96} />
        <div>
          <h1 className="text-2xl font-semibold" data-testid="student-name">
            {s.full_name}
          </h1>
          <p className="text-sm text-muted-foreground">
            {s.student_code} · {s.status}
            {s.user_status && ` · account ${s.user_status.replace('_', ' ')}`}
          </p>
        </div>
      </div>
      <ProblemAlert error={activation.error ?? doExit.error} />
      {activation.isSuccess && (
        <Alert variant="success" data-testid="activation-sent">
          The activation email was sent again to {s.email}. The previous link no longer works.
        </Alert>
      )}
      <Card className="grid gap-2 text-sm sm:grid-cols-2">
        <p>Email: {s.email ?? '—'}</p>
        <p>Phone: {s.phone ?? '—'}</p>
        {'date_of_birth' in s && <p>Date of birth: {showDate(s.date_of_birth)}</p>}
        <p>Admitted: {showDate(s.admission_date)}</p>
        {'guardians' in s && (
          <div className="sm:col-span-2">
            <p className="font-medium">Emergency contacts</p>
            {s.guardians?.map((g) => (
              <p key={g.id}>
                {g.full_name} ({g.relationship}) {g.phone} {g.is_primary && '· primary'}
              </p>
            ))}
          </div>
        )}
      </Card>
      {me.data?.permissions.includes('finance.view') && <StudentFinance studentId={s.id} />}
      {me.data?.permissions.includes('allocations.manage') && <StudentAllocation studentId={s.id} hostelId={s.hostel_id} active={s.status === 'active'} />}
      {canManage && (
        <>
          {s.user_status === 'pending_activation' && (
            <Button variant="outline" onClick={() => activation.mutate()}>
              Send the activation email again
            </Button>
          )}
          <Documents student={s} />
          {s.status === 'active' && (
            <Card className="space-y-2">
              <CardTitle className="text-base">Exit</CardTitle>
              <div className="flex flex-wrap items-end gap-2">
                <Field id="x-date" label="Exit date">
                  <Input id="x-date" type="date" value={exit.exited_on} onChange={(e) => setExit({ ...exit, exited_on: e.target.value })} />
                </Field>
                <Field id="x-reason" label="Reason">
                  <Select id="x-reason" value={exit.exit_reason} onChange={(e) => setExit({ ...exit, exit_reason: e.target.value })}>
                    {EXIT_REASONS.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </Select>
                </Field>
                {owed && (
                  <Field id="x-note" label="Why record the exit anyway?" hint="Kept in the audit log">
                    <Input id="x-note" required minLength={5} value={exit.outstanding_note} onChange={(e) => setExit({ ...exit, outstanding_note: e.target.value })} />
                  </Field>
                )}
                <Button variant="destructive" disabled={!exit.exited_on || (!!owed && exit.outstanding_note.trim().length < 5)} onClick={() => doExit.mutate()}>
                  {owed ? 'Record exit anyway' : 'Record exit'}
                </Button>
              </div>
              {owed ? (
                <Alert variant="error" data-testid="exit-warning">
                  <p className="font-medium">Money is still outstanding.</p>
                  <p>
                    Dues {rupees(String(owed.dues))} · Deposit held {rupees(String(owed.deposit_held))}. Settle them on the Fees card, or give a reason to record the exit anyway. The dues stay on the student&apos;s account.
                  </p>
                </Alert>
              ) : (
                <ProblemAlert error={doExit.error} />
              )}
            </Card>
          )}
        </>
      )}
    </div>
  )
}
