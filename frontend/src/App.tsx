// The router (docs/02 §3): public pages, /o/:slug/* before sign-in, /admin (staff),
// /portal (students), /platform (Platform Owner). Each signed-in area is guarded by
// RequireSession, which restores the session from the refresh cookie after a reload.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router'

import { RequireSession } from '@/auth/session'
import { AdminHomePage, PortalHomePage } from '@/pages/home'
import { NotFoundPage, OrgShell, PlatformLoginPage, PlatformShell, SecurityPage } from '@/pages/app-pages'
import { ActivatePage } from '@/pages/ActivatePage'
import { HostelDetailPage, HostelsPage } from '@/pages/admin/HostelsPage'
import { OrganizationPage } from '@/pages/admin/OrganizationPage'
import { StaffPage } from '@/pages/admin/StaffPage'
import { AdmitStudentPage, StudentDetailPage, StudentsPage } from '@/pages/admin/StudentsPage'
import { PortalProfilePage } from '@/pages/portal/ProfilePage'
import { AllocationsPage } from '@/pages/admin/Allocations'
import { FeeStructuresPage, FinanceQueuesPage } from '@/pages/admin/Finance'
import { PortalFeesPage } from '@/pages/portal/FeesPage'
import { LeaveQueuePage } from '@/pages/admin/Leave'
import { AuditPage } from '@/pages/admin/AuditPage'
import { PlatformAuditPage, PlatformHealthPage, PlatformOrganizationsPage, PlatformSettingsPage } from '@/pages/platform/Console'
import { AnnouncementsPage, PortalAnnouncementsPage } from '@/pages/admin/Announcements'
import { ComplaintsPage } from '@/pages/admin/Complaints'
import { MessPage, PortalMessPage } from '@/pages/admin/Mess'
import { PortalComplaintsPage } from '@/pages/portal/ComplaintsPage'
import { PortalLeavePage } from '@/pages/portal/LeavePage'
import { ForgotPasswordPage, LoginPage, RegisterPage, VerifyRegistrationPage } from '@/pages/auth-pages'
import { LandingPage } from '@/pages/public/LandingPage'
import { PublicLayout } from '@/pages/public/PublicLayout'
import { StaffSignInPage, StudentSignInPage } from '@/pages/public/SignInPages'
import { StudentRegistrationPage, VerifyStudentRequestPage } from '@/pages/public/StudentRegistration'
import { RegistrationRequestsPage } from '@/pages/admin/RegistrationRequests'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
})

export const routes = [
  {
    // The public pages: the front page and the sidebar's destinations, each with its own address.
    element: <PublicLayout />,
    children: [
      { path: '/', element: <LandingPage /> },
      { path: '/sign-in/student', element: <StudentSignInPage /> },
      { path: '/sign-in/staff', element: <StaffSignInPage /> },
      { path: '/register/student', element: <StudentRegistrationPage /> },
      { path: '/register', element: <RegisterPage /> },
      { path: '/forgot-password', element: <ForgotPasswordPage portal="student" /> }, // D80: email only
      { path: '/forgot-password/staff', element: <ForgotPasswordPage portal="staff" /> }, // D78: email only
    ],
  },
  { path: '/o/:slug/verify', element: <VerifyRegistrationPage /> },
  { path: '/o/:slug/login', element: <LoginPage /> },
  { path: '/o/:slug/forgot-password', element: <ForgotPasswordPage /> },
  { path: '/o/:slug/activate', element: <ActivatePage /> }, // the emailed activation link (D80)
  { path: '/o/:slug/register/verify', element: <VerifyStudentRequestPage /> },
  {
    path: '/admin',
    element: (
      <RequireSession realm="org" kind="staff">
        <OrgShell area="admin" />
      </RequireSession>
    ),
    children: [
      { index: true, element: <AdminHomePage /> },
      { path: 'security', element: <SecurityPage /> },
      { path: 'organization', element: <OrganizationPage /> },
      { path: 'hostels', element: <HostelsPage /> },
      { path: 'hostels/:hostelId', element: <HostelDetailPage /> },
      { path: 'staff', element: <StaffPage /> },
      { path: 'students', element: <StudentsPage /> },
      { path: 'students/new', element: <AdmitStudentPage /> },
      { path: 'students/:studentId', element: <StudentDetailPage /> },
      { path: 'registration-requests', element: <RegistrationRequestsPage /> },
      { path: 'allocations', element: <AllocationsPage /> },
      { path: 'fee-structures', element: <FeeStructuresPage /> },
      { path: 'payment-queues', element: <FinanceQueuesPage /> },
      { path: 'leave', element: <LeaveQueuePage /> },
      { path: 'audit', element: <AuditPage /> },
      { path: 'complaints', element: <ComplaintsPage /> },
      { path: 'mess', element: <MessPage /> },
      { path: 'announcements', element: <AnnouncementsPage /> },
    ],
  },
  {
    path: '/portal',
    element: (
      <RequireSession realm="org" kind="student">
        <OrgShell area="portal" />
      </RequireSession>
    ),
    children: [
      { index: true, element: <PortalHomePage /> },
      { path: 'profile', element: <PortalProfilePage /> },
      { path: 'fees', element: <PortalFeesPage /> },
      { path: 'leave', element: <PortalLeavePage /> },
      { path: 'complaints', element: <PortalComplaintsPage /> },
      { path: 'mess', element: <PortalMessPage /> },
      { path: 'announcements', element: <PortalAnnouncementsPage /> },
      { path: 'security', element: <SecurityPage /> },
    ],
  },
  { path: '/platform/login', element: <PlatformLoginPage /> },
  {
    path: '/platform',
    element: (
      <RequireSession realm="platform">
        <PlatformShell />
      </RequireSession>
    ),
    children: [
      { index: true, element: <PlatformOrganizationsPage /> },
      { path: 'health', element: <PlatformHealthPage /> },
      { path: 'audit', element: <PlatformAuditPage /> },
      { path: 'settings', element: <PlatformSettingsPage /> },
    ],
  },
  { path: '*', element: <NotFoundPage /> },
]

const router = createBrowserRouter(routes)

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
