// Plain words for the codes the API stores (review Minor 6). The database keeps short codes such as
// `deposit_applied`; people should never have to read them. A code missing from a map is shown with
// its underscores removed, so a new code added in the backend never appears raw.

const humanize = (code: string) => {
  const words = code.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

const maps = {
  entryType: {
    charge: 'Charge',
    late_fee: 'Late fee',
    fine: 'Fine',
    waiver: 'Waiver',
    payment: 'Payment',
    refund: 'Refund paid',
    deposit_received: 'Deposit received',
    deposit_applied: 'Deposit used for dues',
    deposit_refunded: 'Deposit refunded',
  },
  account: { dues: 'Dues', deposit: 'Deposit' },
  feeKind: { rent: 'Rent', deposit: 'Security deposit', other: 'Other' },
  billingCycle: { monthly: 'Monthly', term: 'Per term', annual: 'Annual', one_time: 'One-time' },
  lateFee: { none: 'None', flat: 'Flat', per_day: 'Per day' },
  payMethod: { cash: 'Cash', upi: 'UPI', bank_transfer: 'Bank transfer' },
  refundStatus: { requested: 'Waiting for approval', approved: 'Approved, not paid yet', rejected: 'Rejected', paid: 'Paid', cancelled: 'Withdrawn' },
  submissionStatus: { pending: 'Waiting for the office', verified: 'Verified', rejected: 'Rejected' },
  userStatus: { active: 'Active', pending_activation: 'Waiting for activation', deactivated: 'Deactivated', read_only: 'Read only' },
  fineReason: { damage: 'Damage', correction: 'Correction', proration: 'Proration', other: 'Other' },
} as const

export type LabelKind = keyof typeof maps

/** The words for `code` of that kind, e.g. label('entryType', 'deposit_applied') = "Deposit used for dues". */
export function label(kind: LabelKind, code: string | null | undefined): string {
  if (!code) return '—'
  return (maps[kind] as Record<string, string>)[code] ?? humanize(code)
}

/** The [code, words] pairs of a kind, for a <select>. */
export const options = (kind: LabelKind): [string, string][] => Object.entries(maps[kind])
