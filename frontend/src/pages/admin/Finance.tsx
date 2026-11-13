// Fees, payments and the ledger in the admin area (docs/04 §12.9).
// Money is always a string in the API ("1250.00"); the UI never does float arithmetic on it.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api, apiBlob } from '@/api/client'
import type { BalanceOut, EntryOut, FeeStructureOut, HostelOut, PaymentOut, RefundOut, SubmissionOut } from '@/api/types'
import { useMe, useOrgToday } from '@/auth/session'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PageHeader, Select, Table, Td, Th } from '@/components/ui/table'
import { firstOfMonth, showDate } from '@/lib/dates'
import { label, options } from '@/lib/labels'
import { duesWords, rupees, splitByDueDate } from '@/lib/money'

export { rupees }

/** The student's payment screenshot, through a short-lived signed link (files router). */
async function openProof(fileId: string) {
  const link = await api<{ url: string }>(`/files/${fileId}/link`)
  window.open(link.url, '_blank', 'noopener')
}

async function openReceipt(path: string) {
  const blob = await apiBlob(path)
  window.open(URL.createObjectURL(blob), '_blank', 'noopener')
}

// --- the finance panel on a student's page -------------------------------------------------------

// Ledger entries an Administrator may reverse here; payments are reversed as payments (FIN-5).
const REVERSIBLE = ['charge', 'late_fee', 'fine', 'waiver', 'deposit_applied']

export function StudentFinance({ studentId }: { studentId: string }) {
  const today = useOrgToday() // the receipt date decides the financial year (Q29): never the UTC date
  const me = useMe()
  const queryClient = useQueryClient()
  const canManage = !!me.data?.permissions.includes('finance.manage')
  const balance = useQuery({ queryKey: ['balance', studentId], queryFn: () => api<BalanceOut>(`/admin/students/${studentId}/balance`) })
  const ledger = useQuery({ queryKey: ['ledger', studentId], queryFn: () => api<EntryOut[]>(`/admin/students/${studentId}/ledger`) })
  const payments = useQuery({ queryKey: ['payments', studentId], queryFn: () => api<PaymentOut[]>(`/admin/payments?student_id=${studentId}`) })
  const refresh = () => {
    for (const key of ['balance', 'ledger', 'payments']) void queryClient.invalidateQueries({ queryKey: [key, studentId] })
  }
  const [payForm, setPayForm] = useState({ method: 'cash', reference: '', dues_amount: '', deposit_amount: '', received_on: today })
  const [key, setKey] = useState(() => crypto.randomUUID()) // one Idempotency-Key per payment attempt (04 §10)
  const pay = useMutation({
    mutationFn: () =>
      api<PaymentOut>('/admin/payments', {
        method: 'POST',
        headers: { 'Idempotency-Key': key },
        body: { student_id: studentId, method: payForm.method, reference: payForm.reference || null, received_on: payForm.received_on, dues_amount: payForm.dues_amount || '0', deposit_amount: payForm.deposit_amount || '0' },
      }),
    onSuccess: () => {
      setKey(crypto.randomUUID())
      setPayForm({ ...payForm, reference: '', dues_amount: '', deposit_amount: '' })
      refresh()
    },
  })
  const [chargeForm, setChargeForm] = useState({ kind: 'charge', amount: '', description: '', due_date: today, reason_code: 'damage', reason_note: '' })
  const addCharge = useMutation({
    mutationFn: () =>
      api(`/admin/students/${studentId}/charges`, {
        method: 'POST',
        body: { ...chargeForm, reason_code: chargeForm.kind === 'fine' ? chargeForm.reason_code : null, reason_note: chargeForm.kind === 'fine' ? chargeForm.reason_note || null : null },
      }),
    onSuccess: refresh,
  })
  // Waiving asks for the amount and a real reason (Q27; review Should fix 8): no one-click "waive it all".
  const [waiving, setWaiving] = useState<{ entry: string; amount: string; reason_code: string; reason_note: string } | null>(null)
  const waive = useMutation({
    mutationFn: (w: { entry: string; amount: string; reason_code: string; reason_note: string }) =>
      api(`/admin/students/${studentId}/waivers`, { method: 'POST', body: { target_entry_id: w.entry, amount: w.amount, reason_code: w.reason_code, reason_note: w.reason_note } }),
    onSuccess: () => {
      setWaiving(null)
      refresh()
    },
  })
  // Reversing a ledger entry, applying the deposit and requesting a refund were API-only (review Critical 8).
  const [reversing, setReversing] = useState<{ id: string; reason: string } | null>(null)
  const reverseEntry = useMutation({
    mutationFn: (r: { id: string; reason: string }) => api(`/admin/ledger-entries/${r.id}/reverse`, { method: 'POST', body: { reason: r.reason } }),
    onSuccess: () => {
      setReversing(null)
      refresh()
    },
  })
  const [applyAmount, setApplyAmount] = useState('')
  const applyDeposit = useMutation({
    mutationFn: () => api(`/admin/students/${studentId}/deposit/apply`, { method: 'POST', body: { amount: applyAmount } }),
    onSuccess: () => {
      setApplyAmount('')
      refresh()
    },
  })
  const [refundForm, setRefundForm] = useState({ account: 'dues', amount: '', reason: '' })
  const requestRefund = useMutation({
    mutationFn: () => api('/admin/refund-requests', { method: 'POST', body: { student_id: studentId, ...refundForm } }),
    onSuccess: () => {
      setRefundForm({ account: 'dues', amount: '', reason: '' })
      void queryClient.invalidateQueries({ queryKey: ['refunds'] })
    },
  })
  const [reversingPayment, setReversingPayment] = useState<{ id: string; reason: string } | null>(null)
  const reverse = useMutation({
    mutationFn: (r: { id: string; reason: string }) => api(`/admin/payments/${r.id}/reverse`, { method: 'POST', body: { reason: r.reason } }),
    onSuccess: () => {
      setReversingPayment(null)
      refresh()
    },
  })
  const split = balance.data ? splitByDueDate(balance.data.open_charges, today) : null
  const b = balance.data
  return (
    <Card className="space-y-4">
      <CardTitle className="text-base">Fees and payments</CardTitle>
      <ProblemAlert error={balance.error ?? pay.error ?? addCharge.error ?? waive.error ?? reverse.error ?? reverseEntry.error ?? applyDeposit.error ?? requestRefund.error} />
      {b && (
        <div className="grid gap-2 text-sm sm:grid-cols-4" data-testid="balance">
          <p>
            {duesWords(b.dues).label}: <strong>{duesWords(b.dues).amount}</strong>
            {split && b.open_charges.length > 0 && (
              <span className="block text-xs text-muted-foreground">
                Overdue {rupees(split.overdue)} · not yet due {rupees(split.upcoming)}
              </span>
            )}
          </p>
          <p>Deposit held: {rupees(b.deposit_held)}</p>
          <p>Deposit due: {rupees(b.deposit_due)}</p>
          <p>Unallocated credit: {rupees(b.unallocated_credit)}</p>
        </div>
      )}
      {!!b?.open_charges.length && (
        <Table>
          <thead>
            <tr>
              <Th>Open charge</Th>
              <Th>Due</Th>
              <Th>Outstanding</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {b.open_charges.map((c) => (
              <tr key={c.entry_id}>
                <Td>{c.description}</Td>
                <Td>
                  {showDate(c.due_date)}
                  {c.due_date && c.due_date < today && <strong className="ml-1 text-destructive">overdue</strong>}
                </Td>
                <Td>
                  {rupees(c.outstanding)} / {rupees(c.amount)}
                </Td>
                <Td>
                  {canManage && (
                    <Button size="sm" variant="ghost" onClick={() => setWaiving({ entry: c.entry_id, amount: c.outstanding, reason_code: 'waiver', reason_note: '' })}>
                      Waive
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {waiving && (
        <form
          className="grid items-end gap-2 rounded-lg border border-border p-3 sm:grid-cols-5"
          onSubmit={(e) => {
            e.preventDefault()
            waive.mutate(waiving)
          }}
        >
          <Field id="w-amount" label="Waiver amount ₹">
            <Input id="w-amount" inputMode="decimal" required value={waiving.amount} onChange={(e) => setWaiving({ ...waiving, amount: e.target.value })} />
          </Field>
          <Field id="w-code" label="Waiver reason">
            <Select id="w-code" value={waiving.reason_code} onChange={(e) => setWaiving({ ...waiving, reason_code: e.target.value })}>
              <option value="waiver">General waiver</option>
              <option value="discount">Discount</option>
              <option value="scholarship">Scholarship</option>
              <option value="proration">Proration</option>
              <option value="correction">Correction</option>
              <option value="other">Other</option>
            </Select>
          </Field>
          <Field id="w-note" label="Waiver note" hint="Why, in a few words (kept in the ledger and the audit log)">
            <Input id="w-note" required minLength={3} value={waiving.reason_note} onChange={(e) => setWaiving({ ...waiving, reason_note: e.target.value })} />
          </Field>
          <Button type="submit" disabled={waive.isPending}>
            Confirm waiver
          </Button>
          <Button type="button" variant="ghost" onClick={() => setWaiving(null)}>
            Cancel
          </Button>
        </form>
      )}
      {canManage && (
        <form
          className="grid items-end gap-2 sm:grid-cols-6"
          onSubmit={(e) => {
            e.preventDefault()
            pay.mutate()
          }}
        >
          <Field id="p-method" label="Method">
            <Select id="p-method" value={payForm.method} onChange={(e) => setPayForm({ ...payForm, method: e.target.value })}>
              <option value="cash">Cash</option>
              <option value="upi">UPI</option>
              <option value="bank_transfer">Bank transfer</option>
            </Select>
          </Field>
          <Field id="p-ref" label="Reference">
            <Input id="p-ref" value={payForm.reference} onChange={(e) => setPayForm({ ...payForm, reference: e.target.value })} />
          </Field>
          <Field id="p-dues" label="Dues ₹">
            <Input id="p-dues" inputMode="decimal" value={payForm.dues_amount} onChange={(e) => setPayForm({ ...payForm, dues_amount: e.target.value })} />
          </Field>
          <Field id="p-dep" label="Deposit ₹">
            <Input id="p-dep" inputMode="decimal" value={payForm.deposit_amount} onChange={(e) => setPayForm({ ...payForm, deposit_amount: e.target.value })} />
          </Field>
          <Field id="p-date" label="Received on">
            <Input id="p-date" type="date" value={payForm.received_on} onChange={(e) => setPayForm({ ...payForm, received_on: e.target.value })} />
          </Field>
          <Button type="submit" disabled={pay.isPending}>
            Record payment
          </Button>
        </form>
      )}
      {canManage && (
        <form
          className="grid items-end gap-2 sm:grid-cols-7"
          onSubmit={(e) => {
            e.preventDefault()
            addCharge.mutate()
          }}
        >
          <Field id="c-kind" label="Type">
            <Select id="c-kind" value={chargeForm.kind} onChange={(e) => setChargeForm({ ...chargeForm, kind: e.target.value })}>
              <option value="charge">Charge</option>
              <option value="fine">Fine</option>
            </Select>
          </Field>
          <Field id="c-desc" label="Description">
            <Input id="c-desc" value={chargeForm.description} onChange={(e) => setChargeForm({ ...chargeForm, description: e.target.value })} />
          </Field>
          <Field id="c-amount" label="Amount ₹">
            <Input id="c-amount" inputMode="decimal" value={chargeForm.amount} onChange={(e) => setChargeForm({ ...chargeForm, amount: e.target.value })} />
          </Field>
          <Field id="c-due" label="Due">
            <Input id="c-due" type="date" value={chargeForm.due_date} onChange={(e) => setChargeForm({ ...chargeForm, due_date: e.target.value })} />
          </Field>
          {chargeForm.kind === 'fine' && (
            <>
              <Field id="c-code" label="Fine for">
                <Select id="c-code" value={chargeForm.reason_code} onChange={(e) => setChargeForm({ ...chargeForm, reason_code: e.target.value })}>
                  {options('fineReason').map(([code, words]) => (
                    <option key={code} value={code}>
                      {words}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field id="c-note" label="Fine note">
                <Input id="c-note" required minLength={3} value={chargeForm.reason_note} onChange={(e) => setChargeForm({ ...chargeForm, reason_note: e.target.value })} />
              </Field>
            </>
          )}
          <Button type="submit" variant="outline" disabled={addCharge.isPending}>
            Add
          </Button>
        </form>
      )}
      {canManage && b && (
        <div className="grid gap-3 sm:grid-cols-2">
          <form
            className="grid items-end gap-2 rounded-lg border border-border p-3 sm:grid-cols-2"
            onSubmit={(e) => {
              e.preventDefault()
              applyDeposit.mutate()
            }}
          >
            <p className="text-sm font-medium sm:col-span-2">Apply deposit to dues (e.g. at exit)</p>
            <Field id="d-apply" label="Amount to apply ₹" hint={`Deposit held ${rupees(b.deposit_held)}`}>
              <Input id="d-apply" inputMode="decimal" required value={applyAmount} onChange={(e) => setApplyAmount(e.target.value)} />
            </Field>
            <Button type="submit" variant="outline" disabled={applyDeposit.isPending}>
              Apply deposit
            </Button>
          </form>
          <form
            className="grid items-end gap-2 rounded-lg border border-border p-3 sm:grid-cols-2"
            onSubmit={(e) => {
              e.preventDefault()
              requestRefund.mutate()
            }}
          >
            <p className="text-sm font-medium sm:col-span-2">Request a refund (then approve it in Payment queues; marked self-approved)</p>
            <Field id="r-account" label="Refund from">
              <Select id="r-account" value={refundForm.account} onChange={(e) => setRefundForm({ ...refundForm, account: e.target.value })}>
                <option value="dues">Credit on dues</option>
                <option value="deposit">Deposit</option>
              </Select>
            </Field>
            <Field id="r-amount" label="Refund amount ₹">
              <Input id="r-amount" inputMode="decimal" required value={refundForm.amount} onChange={(e) => setRefundForm({ ...refundForm, amount: e.target.value })} />
            </Field>
            <Field id="r-reason" label="Refund reason">
              <Input id="r-reason" required minLength={5} value={refundForm.reason} onChange={(e) => setRefundForm({ ...refundForm, reason: e.target.value })} />
            </Field>
            <Button type="submit" variant="outline" disabled={requestRefund.isPending}>
              Request refund
            </Button>
            {requestRefund.isSuccess && <p className="text-sm text-success sm:col-span-2">Requested. It now waits in Payment queues for approval.</p>}
          </form>
        </div>
      )}
      <details className="text-sm">
        <summary className="cursor-pointer text-muted-foreground">Payments ({payments.data?.length ?? 0})</summary>
        <ul className="mt-1 space-y-1">
          {payments.data?.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center gap-2">
              {p.receipt_number} · {showDate(p.received_on)} · {label('payMethod', p.method)} · {rupees(p.amount)} {p.reversed && <strong className="text-destructive">reversed</strong>}
              <Button size="sm" variant="ghost" onClick={() => void openReceipt(`/admin/payments/${p.id}/receipt.pdf`)}>
                Receipt
              </Button>
              {canManage && !p.reversed && (
                <Button size="sm" variant="ghost" onClick={() => setReversingPayment({ id: p.id, reason: '' })}>
                  Reverse
                </Button>
              )}
              {reversingPayment?.id === p.id && (
                <form
                  className="flex items-end gap-2"
                  onSubmit={(ev) => {
                    ev.preventDefault()
                    reverse.mutate(reversingPayment)
                  }}
                >
                  <Field id="pv-reason" label="Reason for reversing the payment">
                    <Input id="pv-reason" required minLength={5} value={reversingPayment.reason} onChange={(ev) => setReversingPayment({ ...reversingPayment, reason: ev.target.value })} />
                  </Field>
                  <Button type="submit" size="sm" disabled={reverse.isPending}>
                    Confirm reversal
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setReversingPayment(null)}>
                    Cancel
                  </Button>
                </form>
              )}
            </li>
          ))}
        </ul>
      </details>
      <details className="text-sm" open>
        <summary className="cursor-pointer text-muted-foreground">Ledger ({ledger.data?.length ?? 0} entries)</summary>
        <ul className="mt-1 space-y-1 text-xs" data-testid="ledger">
          {ledger.data?.map((e) => (
            <li key={e.id} className="flex flex-wrap items-center gap-2">
              <span>
                {showDate(e.effective_date)} · {label('account', e.account)} · {label('entryType', e.entry_type)} · <span className="font-mono">{rupees(e.amount)}</span> · {e.description} {e.reversed && '(reversed)'}
              </span>
              {canManage && REVERSIBLE.includes(e.entry_type) && !e.reversed && !e.payment_id && (
                <Button size="sm" variant="ghost" onClick={() => setReversing({ id: e.id, reason: '' })}>
                  Reverse
                </Button>
              )}
              {reversing?.id === e.id && (
                <form
                  className="flex items-end gap-2 font-sans"
                  onSubmit={(ev) => {
                    ev.preventDefault()
                    reverseEntry.mutate(reversing)
                  }}
                >
                  <Field id="rv-reason" label="Reason for the reversal">
                    <Input id="rv-reason" required minLength={5} value={reversing.reason} onChange={(ev) => setReversing({ ...reversing, reason: ev.target.value })} />
                  </Field>
                  <Button type="submit" size="sm" disabled={reverseEntry.isPending}>
                    Confirm reversal
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setReversing(null)}>
                    Cancel
                  </Button>
                </form>
              )}
            </li>
          ))}
        </ul>
      </details>
    </Card>
  )
}

// --- fee structures and charge runs ---------------------------------------------------------------------

export function FeeStructuresPage() {
  const today = useOrgToday()
  const queryClient = useQueryClient()
  const list = useQuery({ queryKey: ['fee-structures'], queryFn: () => api<FeeStructureOut[]>('/admin/fee-structures') })
  const hostels = useQuery({ queryKey: ['hostels'], queryFn: () => api<HostelOut[]>('/admin/hostels') })
  const [f, setF] = useState({ name: '', hostel_id: '', fee_kind: 'rent', amount: '', billing_cycle: 'monthly', due_days: '10', late_fee_type: 'none', late_fee_amount: '', late_fee_cap: '' })
  const [period, setPeriod] = useState(firstOfMonth(today))
  const create = useMutation({
    mutationFn: () =>
      api('/admin/fee-structures', {
        method: 'POST',
        body: {
          name: f.name,
          hostel_id: f.hostel_id || null,
          fee_kind: f.fee_kind,
          amount: f.amount,
          billing_cycle: f.fee_kind === 'deposit' ? 'one_time' : f.billing_cycle,
          due_days: Number(f.due_days),
          late_fee_type: f.late_fee_type,
          late_fee_amount: f.late_fee_type === 'none' ? null : f.late_fee_amount,
          late_fee_cap: f.late_fee_type === 'per_day' ? f.late_fee_cap : null,
        },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['fee-structures'] }),
  })
  const run = useMutation({ mutationFn: (id: string) => api<{ status: string }>('/admin/charge-runs', { method: 'POST', body: { fee_structure_id: id, period_start: period } }) })
  const set = (k: string) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value })
  return (
    <div className="max-w-5xl space-y-6">
      <PageHeader title="Fee structures" />
      <ProblemAlert error={list.error ?? create.error ?? run.error} />
      {run.data && <p className="text-sm text-success">Charge run {run.data.status}. Charges appear in a few seconds.</p>}
      <div className="flex items-end gap-2">
        <Field id="period" label="Charge period starting" hint="Monthly fees: the 1st of the month (the period is that calendar month).">
          <Input id="period" type="date" value={period} onChange={(e) => setPeriod(e.target.value)} />
        </Field>
      </div>
      <Table>
        <thead>
          <tr>
            <Th>Name</Th>
            <Th>Kind</Th>
            <Th>Amount</Th>
            <Th>Cycle</Th>
            <Th>Late fee</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {list.data?.map((s) => (
            <tr key={s.id}>
              <Td>{s.name}</Td>
              <Td>{label('feeKind', s.fee_kind)}</Td>
              <Td>{rupees(s.amount)}</Td>
              <Td>{label('billingCycle', s.billing_cycle)}</Td>
              <Td>{s.late_fee_type === 'none' ? '—' : `${label('lateFee', s.late_fee_type)} ${s.late_fee_amount ? rupees(s.late_fee_amount) : ''}`}</Td>
              <Td>
                {s.fee_kind !== 'deposit' && (
                  <Button size="sm" variant="outline" onClick={() => run.mutate(s.id)}>
                    Generate charges
                  </Button>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      <Card>
        <form
          className="grid items-end gap-2 sm:grid-cols-5"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate()
          }}
        >
          <Field id="f-name" label="Name">
            <Input id="f-name" value={f.name} onChange={set('name')} />
          </Field>
          <Field id="f-hostel" label="Hostel">
            <Select id="f-hostel" value={f.hostel_id} onChange={set('hostel_id')}>
              <option value="">All hostels</option>
              {hostels.data?.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.code}
                </option>
              ))}
            </Select>
          </Field>
          <Field id="f-kind" label="Kind">
            <Select id="f-kind" value={f.fee_kind} onChange={set('fee_kind')}>
              <option value="rent">Rent</option>
              <option value="deposit">Security deposit</option>
              <option value="other">Other</option>
            </Select>
          </Field>
          <Field id="f-amount" label="Amount ₹">
            <Input id="f-amount" inputMode="decimal" value={f.amount} onChange={set('amount')} />
          </Field>
          <Field id="f-cycle" label="Billing cycle">
            <Select id="f-cycle" value={f.billing_cycle} onChange={set('billing_cycle')}>
              <option value="monthly">Monthly</option>
              <option value="term">Per term</option>
              <option value="annual">Annual</option>
              <option value="one_time">One-time</option>
            </Select>
          </Field>
          <Field id="f-due" label="Due after (days)">
            <Input id="f-due" type="number" value={f.due_days} onChange={set('due_days')} />
          </Field>
          <Field id="f-late" label="Late fee">
            <Select id="f-late" value={f.late_fee_type} onChange={set('late_fee_type')}>
              <option value="none">None</option>
              <option value="flat">Flat</option>
              <option value="per_day">Per day (capped)</option>
            </Select>
          </Field>
          {f.late_fee_type !== 'none' && (
            <Field id="f-late-amount" label="Late fee ₹">
              <Input id="f-late-amount" value={f.late_fee_amount} onChange={set('late_fee_amount')} />
            </Field>
          )}
          {f.late_fee_type === 'per_day' && (
            <Field id="f-cap" label="Cap ₹">
              <Input id="f-cap" value={f.late_fee_cap} onChange={set('late_fee_cap')} />
            </Field>
          )}
          <Button type="submit" disabled={create.isPending}>
            Add fee structure
          </Button>
        </form>
      </Card>
    </div>
  )
}

// --- the payment queues: UPI submissions and refunds --------------------------------------------------------

export function FinanceQueuesPage() {
  const queryClient = useQueryClient()
  const subs = useQuery({ queryKey: ['submissions'], queryFn: () => api<SubmissionOut[]>('/admin/payment-submissions') })
  const refunds = useQuery({ queryKey: ['refunds'], queryFn: () => api<RefundOut[]>('/admin/refund-requests') })
  const refresh = () => queryClient.invalidateQueries()
  // One small form at a time, shown under the row it belongs to (review Minor 10: no window.prompt).
  const [rejecting, setRejecting] = useState<{ kind: 'submission' | 'refund'; id: string; note: string } | null>(null)
  const [paying, setPaying] = useState<{ id: string; method: string; reference: string } | null>(null)
  const done = () => {
    setRejecting(null)
    setPaying(null)
    void refresh()
  }
  const review = useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) =>
      api(`/admin/payment-submissions/${id}/${note === undefined ? 'verify' : 'reject'}`, { method: 'POST', body: note === undefined ? undefined : { note } }),
    onSuccess: done,
  })
  const decide = useMutation({
    mutationFn: ({ id, action, body }: { id: string; action: 'approve' | 'reject' | 'cancel' | 'mark-paid'; body?: Record<string, unknown> }) =>
      api(`/admin/refund-requests/${id}/${action}`, { method: 'POST', body: action === 'cancel' ? undefined : (body ?? {}) }),
    onSuccess: done,
  })
  const rejectForm = rejecting && (
    <form
      className="flex flex-wrap items-end gap-2 rounded-lg border border-border p-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (rejecting.kind === 'submission') review.mutate({ id: rejecting.id, note: rejecting.note })
        else decide.mutate({ id: rejecting.id, action: 'reject', body: { note: rejecting.note } })
      }}
    >
      <Field id="rj-note" label="Why is it rejected?" hint="Kept with the request and in the audit log.">
        <Input id="rj-note" required minLength={3} value={rejecting.note} onChange={(e) => setRejecting({ ...rejecting, note: e.target.value })} />
      </Field>
      <Button type="submit" size="sm" disabled={review.isPending || decide.isPending}>
        Confirm rejection
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => setRejecting(null)}>
        Cancel
      </Button>
    </form>
  )
  return (
    <div className="max-w-5xl space-y-6">
      <PageHeader title="Payment queues" />
      <ProblemAlert error={subs.error ?? review.error ?? decide.error} />
      <Card className="space-y-2">
        <CardTitle className="text-base">UPI payments to verify</CardTitle>
        <p className="text-xs text-muted-foreground">Check each UTR against the bank statement before verifying (Q28).</p>
        {subs.data?.length === 0 && <p className="text-sm text-muted-foreground">Nothing waiting.</p>}
        {subs.data?.map((s) => (
          <div key={s.id} className="flex flex-wrap items-center gap-2 text-sm">
            <strong>
              {s.student_name} ({s.student_code})
            </strong>
            · {rupees(s.amount)} · UTR {s.utr} · paid {showDate(s.paid_on)}
            {s.proof_file_id && (
              <Button size="sm" variant="ghost" onClick={() => void openProof(s.proof_file_id!)}>
                View proof
              </Button>
            )}
            <Button size="sm" onClick={() => review.mutate({ id: s.id })}>
              Verify
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRejecting({ kind: 'submission', id: s.id, note: '' })}>
              Reject
            </Button>
            {rejecting?.kind === 'submission' && rejecting.id === s.id && <div className="basis-full">{rejectForm}</div>}
          </div>
        ))}
      </Card>
      <Card className="space-y-2" data-testid="refund-queue">
        <CardTitle className="text-base">Refund requests</CardTitle>
        <p className="text-xs text-muted-foreground">
          The organization has one administrator, who approves their own refund requests: each approval is marked “self-approved” and recorded in the audit log (Q30, D76).
        </p>
        {refunds.data?.map((r) => (
          <div key={r.id} className="flex flex-wrap items-center gap-2 text-sm">
            <strong>
              {r.student_name} ({r.student_code})
            </strong>
            · {r.account === 'deposit' ? 'From the deposit' : 'From credit on dues'} · {rupees(r.amount)} · {r.reason} · <strong>{label('refundStatus', r.status)}</strong>
            {r.status === 'paid' && r.paid_method && ` (${label('payMethod', r.paid_method)})`}
            {r.self_approved && <span className="text-xs text-muted-foreground">(self-approved)</span>}
            {r.status === 'requested' && (
              <>
                <Button size="sm" onClick={() => decide.mutate({ id: r.id, action: 'approve' })}>
                  Approve
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setRejecting({ kind: 'refund', id: r.id, note: '' })}>
                  Reject
                </Button>
                <Button size="sm" variant="ghost" onClick={() => decide.mutate({ id: r.id, action: 'cancel' })}>
                  Withdraw
                </Button>
              </>
            )}
            {r.status === 'approved' && paying?.id !== r.id && (
              <Button size="sm" variant="outline" onClick={() => setPaying({ id: r.id, method: 'cash', reference: '' })}>
                Mark paid
              </Button>
            )}
            {rejecting?.kind === 'refund' && rejecting.id === r.id && <div className="basis-full">{rejectForm}</div>}
            {paying?.id === r.id && (
              <form
                className="flex basis-full flex-wrap items-end gap-2 rounded-lg border border-border p-2"
                onSubmit={(e) => {
                  e.preventDefault()
                  decide.mutate({ id: r.id, action: 'mark-paid', body: { method: paying.method, reference: paying.method === 'cash' ? null : paying.reference } })
                }}
              >
                <Field id="rp-method" label="Refund paid by">
                  <Select id="rp-method" value={paying.method} onChange={(e) => setPaying({ ...paying, method: e.target.value })}>
                    {options('payMethod').map(([code, words]) => (
                      <option key={code} value={code}>
                        {words}
                      </option>
                    ))}
                  </Select>
                </Field>
                {paying.method !== 'cash' && (
                  <Field id="rp-ref" label="Refund reference" hint="The UTR or bank reference of the transfer">
                    <Input id="rp-ref" required maxLength={40} value={paying.reference} onChange={(e) => setPaying({ ...paying, reference: e.target.value })} />
                  </Field>
                )}
                <Button type="submit" size="sm" disabled={decide.isPending}>
                  Confirm payment
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={() => setPaying(null)}>
                  Cancel
                </Button>
              </form>
            )}
          </div>
        ))}
      </Card>
    </div>
  )
}
