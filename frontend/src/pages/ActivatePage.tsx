// Account activation from an emailed or copied link (docs/04 §5.7). The token is in the URL
// fragment (#token=...), which the browser never sends to a server or in a Referer header.
import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useLocation, useParams } from 'react-router'

import { api } from '@/api/client'
import type { ActivationPreviewOut } from '@/api/types'
import { ProblemAlert } from '@/components/ProblemAlert'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { AuthLayout } from '@/pages/auth-pages'

export function ActivatePage() {
  const { slug = '' } = useParams()
  const location = useLocation()
  const token = new URLSearchParams(location.hash.replace(/^#/, '')).get('token') ?? ''
  const [password, setPassword] = useState('')
  const preview = useQuery({
    queryKey: ['activation', token],
    queryFn: () => api<ActivationPreviewOut>(`/o/${slug}/auth/activate/preview`, { method: 'POST', auth: false, body: { token } }),
    retry: false,
  })
  const activate = useMutation({ mutationFn: () => api(`/o/${slug}/auth/activate`, { method: 'POST', auth: false, body: { token, password } }) })

  if (activate.isSuccess) {
    return (
      <AuthLayout title="Account activated">
        <Alert variant="success">
          Your password is set.{' '}
          <Link className="underline" to={`/o/${slug}/login`}>
            Sign in
          </Link>
        </Alert>
      </AuthLayout>
    )
  }
  return (
    <AuthLayout title="Activate your account" description={preview.data ? `Welcome, ${preview.data.full_name}. Choose a password.` : undefined}>
      <ProblemAlert error={preview.error ?? activate.error} />
      {preview.data && (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            activate.mutate()
          }}
        >
          <Field id="password" label="Password" hint="At least 8 characters. A phrase works well.">
            <Input id="password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Button type="submit" className="w-full" disabled={!password || activate.isPending}>
            Activate
          </Button>
        </form>
      )}
    </AuthLayout>
  )
}
