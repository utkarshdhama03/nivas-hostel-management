// The student's fees (docs/04 §12.18, Q28): balance, open charges, receipts, and paying by UPI:
// the organization's UPI QR with the amount filled in, then the UTR (and optional screenshot)
// submitted for an administrator to verify. No payment gateway.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api, apiBlob, apiUpload } from '@/api/client'
import type { PortalFeesOut, UploadOut } from '@/api/types'
import { useOrgToday } from '@/auth/session'
import { ProblemAlert } from '@/components/ProblemAlert'
import { QrCode } from '@/components/QrCode'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PageHeader } from '@/components/ui/table'
import { showDate } from '@/lib/dates'
import { label } from '@/lib/labels'
import { duesWords, rupees, splitByDueDate } from '@/lib/money'

export function PortalFeesPage() {
  const queryClient = useQueryClient()
  const fees = useQuery({ queryKey: ['my-fees'], queryFn: () => api<PortalFeesOut>('/portal/me/fees') })
  const today = useOrgToday()
  const [utr, setUtr] = useState('')
  const [amount, setAmount] = useState('')
  const [proof, setProof] = useState<File | null>(null)
  const submit = useMutation({
    mutationFn: async () => {
      let proof_file_id: string | null = null
      if (proof) {
        const form = new FormData()
        form.append('category', 'payment_proof')
        form.append('file', proof)
        proof_file_id = (await apiUpload<UploadOut>('/files', form)).file_id
      }
      return api('/portal/me/payment-submissions', { method: 'POST', body: { amount: amount || fees.data?.balance.dues, utr, paid_on: today, proof_file_id } })
    },
    onSuccess: () => {
      setUtr('')
      void queryClient.invalidateQueries({ queryKey: ['my-fees'] })
    },
  })
  const d = fees.data
  if (fees.error) return <ProblemAlert error={fees.error} />
  if (!d) return null
  // "Due now" used to be the whole balance, including charges not due yet (review Minor 7).
  const split = splitByDueDate(d.balance.open_charges, today)
  const total = duesWords(d.balance.dues)
  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader title="My fees" />
      <Card className="grid gap-2 text-sm sm:grid-cols-3" data-testid="my-balance">
        {total.label === 'Credit' ? (
          <p className="sm:col-span-3">
            Credit: <strong>{total.amount}</strong> <span className="text-muted-foreground">(paid in advance; it is used for your next charge)</span>
          </p>
        ) : (
          <>
            <p>
              Overdue: <strong className={split.overdue === '0.00' ? '' : 'text-destructive'}>{rupees(split.overdue)}</strong>
            </p>
            <p>Not yet due: {rupees(split.upcoming)}</p>
            <p>
              Total dues: <strong>{total.amount}</strong>
            </p>
          </>
        )}
        <p>Deposit held: {rupees(d.balance.deposit_held)}</p>
        <p>Deposit still due: {rupees(d.balance.deposit_due)}</p>
      </Card>
      {d.balance.open_charges.length > 0 && (
        <Card className="space-y-1 text-sm">
          <CardTitle className="text-base">Open charges</CardTitle>
          {d.balance.open_charges.map((c) => (
            <p key={c.entry_id}>
              {c.description} · due {showDate(c.due_date)} · {rupees(c.outstanding)}
              {c.due_date && c.due_date < today && <strong className="ml-1 text-destructive">overdue</strong>}
            </p>
          ))}
        </Card>
      )}
      {d.upi && (
        <Card className="space-y-3">
          <CardTitle className="text-base">Pay by UPI</CardTitle>
          <div className="flex flex-wrap items-center gap-4">
            <QrCode value={d.upi.link} label="UPI payment QR code" />
            <div className="space-y-1 text-sm">
              <p>
                UPI ID: <strong>{d.upi.upi_id}</strong> ({d.upi.payee_name})
              </p>
              <p>Amount: {rupees(d.upi.amount)}</p>
              <a className="text-primary underline" href={d.upi.link}>
                Open in a UPI app
              </a>
            </div>
          </div>
          <Alert>After paying, enter the UTR (transaction reference) from your UPI app. The office checks it against the bank statement.</Alert>
          <ProblemAlert error={submit.error} />
          <div className="grid items-end gap-2 sm:grid-cols-4">
            <Field id="u-utr" label="UTR">
              <Input id="u-utr" value={utr} onChange={(e) => setUtr(e.target.value)} />
            </Field>
            <Field id="u-amount" label="Amount ₹">
              <Input id="u-amount" placeholder={d.upi.amount} value={amount} onChange={(e) => setAmount(e.target.value)} />
            </Field>
            <Field id="u-proof" label="Screenshot (optional)">
              <Input id="u-proof" type="file" accept="image/*,application/pdf" onChange={(e) => setProof(e.target.files?.[0] ?? null)} />
            </Field>
            <Button onClick={() => submit.mutate()} disabled={!utr || submit.isPending}>
              Submit
            </Button>
          </div>
        </Card>
      )}
      {d.submissions.length > 0 && (
        <Card className="space-y-1 text-sm">
          <CardTitle className="text-base">Submitted UPI payments</CardTitle>
          {d.submissions.map((s) => (
            <p key={s.id}>
              {showDate(s.paid_on)} · UTR {s.utr} · {rupees(s.amount)} · {label('submissionStatus', s.status)}
              {s.review_note && ` (${s.review_note})`}
            </p>
          ))}
        </Card>
      )}
      <Card className="space-y-1 text-sm">
        <CardTitle className="text-base">Receipts</CardTitle>
        {d.payments.length === 0 && <p className="text-muted-foreground">No payments yet.</p>}
        {d.payments.map((p) => (
          <p key={p.id} className="flex items-center gap-2">
            {p.receipt_number} · {showDate(p.received_on)} · {rupees(p.amount)} {p.reversed && '(reversed)'}
            <Button
              size="sm"
              variant="ghost"
              onClick={async () => {
                const blob = await apiBlob(`/portal/me/payments/${p.id}/receipt.pdf`)
                window.open(URL.createObjectURL(blob), '_blank', 'noopener')
              }}
            >
              Download
            </Button>
          </p>
        ))}
      </Card>
    </div>
  )
}
