// The Administrator (docs/04 §12.6; D75 one role, D76 one Administrator). Each organization has exactly
// one Administrator: the person who registered it. Nobody can add a second one (the database refuses it
// too), so this page only shows the account. Staff accounts closed by D75/D76 are listed for reference.
// A forgotten password is reset with Forgot password (an emailed 6-digit code, D77).
import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { StaffOut } from '@/api/types'
import { useMe } from '@/auth/session'
import { ProblemAlert } from '@/components/ProblemAlert'
import { PageHeader, Table, Td, Th } from '@/components/ui/table'
import { label } from '@/lib/labels'

export function StaffPage() {
  const me = useMe()
  const staff = useQuery({ queryKey: ['staff'], queryFn: () => api<{ items: StaffOut[] }>('/admin/staff?page_size=100') })

  return (
    <div className="max-w-5xl space-y-6">
      <PageHeader title="Administrator" />
      <div className="space-y-1 text-sm text-muted-foreground" data-testid="administrator-rule">
        <p>This organization has one administrator: the person who registered it. The administrator does everything, in every hostel, and signs in with the email and the password.</p>
        <p>There is no way to add a second administrator. A forgotten password is reset with Forgot password, by a 6-digit code sent to the email.</p>
      </div>
      <ProblemAlert error={staff.error} />
      <Table>
        <thead>
          <tr>
            <Th>ID</Th>
            <Th>Name</Th>
            <Th>Status</Th>
          </tr>
        </thead>
        <tbody>
          {staff.data?.items.map((s) => (
            <tr key={s.id}>
              <Td>{s.employee_code}</Td>
              <Td>
                {s.full_name}
                {s.user_id === me.data?.id && <span className="ml-1 text-xs text-muted-foreground">(you)</span>}
                <p className="text-xs text-muted-foreground">{[s.designation, s.email].filter(Boolean).join(' · ')}</p>
              </Td>
              <Td>{label('userStatus', s.status)}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  )
}
