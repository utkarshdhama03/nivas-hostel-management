import { ApiError } from '@/api/client'
import { Alert } from '@/components/ui/alert'

const FRIENDLY: Record<string, string> = {
  invalid_credentials: 'Wrong ID, email or password.',
  too_many_attempts: 'Too many attempts. Wait a few minutes and try again.',
  link_expired: 'This link has expired or was already used. Ask for a new one.',
  invalid_reset_code: "That code didn't work. Check the code in the email. A code works once, for 10 minutes; after 5 wrong tries ask for a new one.",
  session_invalid: 'Your session ended. Please sign in again.',
  slug_taken: 'This organization address is taken. Choose another.',
  admin_email_taken: "This email already belongs to an organization's administrator. Use another email.",
  organization_suspended: 'This organization is suspended. Contact the platform administrator.',
  account_deactivated: 'This account is not active. Contact your administrator.',
  retry_conflict: 'Someone else made a change at the same moment. Please try again.',
}

const RATE_LIMITED = new Set(['too_many_attempts', 'rate_limited'])

// D74: a registration request's state, told on Student sign-in after the correct password. The
// server's title and its detail (for a rejection: the administrator's reason) are both shown.
const REGISTRATION = new Set(['registration_unverified', 'registration_pending', 'registration_rejected'])

/** The main line. For rate limits with a known wait: "Try again in N minutes." (review Should fix 9). */
function message(p: ApiError['problem']): string {
  const wait = p.retry_after_seconds
  if (RATE_LIMITED.has(p.code) && typeof wait === 'number') {
    const minutes = Math.max(1, Math.ceil(wait / 60))
    return `Too many attempts. Try again in ${minutes} minute${minutes === 1 ? '' : 's'}.`
  }
  return FRIENDLY[p.code] ?? p.detail ?? p.title
}

/** Shows an API problem (docs/04 §7): a friendly message, field errors, and the request ID
 *  people can quote when reporting a 500. */
export function ProblemAlert({ error }: { error: unknown }) {
  if (!error) return null
  if (!(error instanceof ApiError)) return <Alert variant="error">Something went wrong. Check your connection and try again.</Alert>
  const p = error.problem
  if (REGISTRATION.has(p.code)) {
    return (
      <Alert variant={p.code === 'registration_rejected' ? 'error' : 'info'} data-testid="registration-status">
        <p className="font-medium">{p.title}</p>
        {p.detail && <p className="mt-1">{p.detail}</p>}
      </Alert>
    )
  }
  return (
    <Alert variant="error">
      <p className="font-medium">{message(p)}</p>
      {p.errors.length > 0 && (
        <ul className="mt-1 list-disc pl-5">
          {p.errors.map((e) => (
            <li key={`${e.field}-${e.code}`}>{e.message}</li>
          ))}
        </ul>
      )}
      {p.status >= 500 && p.request_id && <p className="mt-1 text-xs">Reference: {p.request_id}</p>}
    </Alert>
  )
}
