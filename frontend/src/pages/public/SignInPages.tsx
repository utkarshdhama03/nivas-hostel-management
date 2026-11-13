// Sign-in on the public pages (no organization in the address). They use the same
// sign-in form as /o/:slug/login (components/SignInForm), which asks for the organization sign-in name.
import { Link } from 'react-router'

import { SignedOutNotice } from '@/auth/session'
import { SignInForm } from '@/components/SignInForm'
import { AuthLayout } from '@/pages/auth-pages'

export function StudentSignInPage() {
  return (
    <AuthLayout title="Student sign-in" description="See your fees, leave and complaints.">
      <SignInForm portal="student" header={<SignedOutNotice />} />
      <p className="text-center text-sm text-muted-foreground">
        New student?{' '}
        <Link className="text-primary underline" to="/register/student">
          Ask to register
        </Link>
        . Registered already? Sign in with your email to see how your request stands.
      </p>
      <p className="text-center text-sm text-muted-foreground">Got an activation email from your hostel? Open the link in it.</p>
    </AuthLayout>
  )
}

export const ORGANIZATION_SIGN_IN_HELP = "For the organization's administrator"

/** "Organization sign-in": the staff portal (any staff role). The name only; roles and permissions are unchanged. */
export function StaffSignInPage() {
  return (
    <AuthLayout title="Organization sign-in" description={ORGANIZATION_SIGN_IN_HELP}>
      <SignInForm portal="staff" header={<SignedOutNotice />} />
      <p className="text-center text-sm text-muted-foreground">
        New organization?{' '}
        <Link className="text-primary underline" to="/register">
          Register it
        </Link>
      </p>
    </AuthLayout>
  )
}
