// The public pages' frame: a sidebar (sign-in, student registration, organization registration, and a small link to the
// platform console) and the page itself. On a phone the sidebar folds away behind a "Menu" button.
import { Building2, GraduationCap, LogIn, Menu, ShieldCheck, UserPlus, X } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { Link, NavLink, Outlet } from 'react-router'

import { ThemeToggle } from '@/components/ThemeToggle'
import { Button } from '@/components/ui/button'
import { SITE } from '@/config/site'
import { InPublicLayout } from '@/pages/public/context'

export const PUBLIC_LINKS: { to: string; label: string; icon: ReactNode }[] = [
  { to: '/sign-in/student', label: 'Student sign-in', icon: <GraduationCap size={16} aria-hidden /> },
  // D74 (11 November): student registration replaces the activation help page. The activation link in
  // the email still opens /o/{name}/activate.
  { to: '/register/student', label: 'Student registration', icon: <UserPlus size={16} aria-hidden /> },
  { to: '/sign-in/staff', label: 'Organization sign-in', icon: <LogIn size={16} aria-hidden /> },
  { to: '/register', label: 'Register your organization', icon: <Building2 size={16} aria-hidden /> },
]

function Brand() {
  return (
    <Link to="/" className="inline-flex items-center gap-2 rounded-md text-lg font-semibold tracking-tight focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <span aria-hidden className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-foreground">
        {SITE.productName.charAt(0)}
      </span>
      {SITE.productName}
    </Link>
  )
}

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-2 rounded-lg px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
    isActive ? 'bg-primary/10 font-medium text-primary' : 'text-foreground hover:bg-muted'
  }`

export function PublicLayout() {
  const [open, setOpen] = useState(false)
  const close = () => setOpen(false)
  return (
    <InPublicLayout.Provider value={true}>
      <div className="min-h-screen md:flex">
        <div className="flex items-center justify-between border-b border-border bg-card px-4 py-3 md:hidden">
          <Brand />
          <Button variant="outline" size="sm" aria-expanded={open} aria-controls="public-nav" onClick={() => setOpen(!open)}>
            {open ? <X size={16} aria-hidden /> : <Menu size={16} aria-hidden />}
            Menu
          </Button>
        </div>
        <aside id="public-nav" className={`${open ? 'block' : 'hidden'} border-b border-border bg-card md:block md:w-64 md:shrink-0 md:border-r md:border-b-0`}>
          <nav aria-label="Sign in and registration" className="flex flex-col gap-1 p-4 md:sticky md:top-0 md:h-screen">
            <div className="mb-4 hidden md:block">
              <Brand />
            </div>
            {PUBLIC_LINKS.map((l) => (
              <NavLink key={l.to} to={l.to} end className={linkClass} onClick={close}>
                {l.icon}
                {l.label}
              </NavLink>
            ))}
            <div className="mt-6 flex items-center justify-between gap-2 border-t border-border pt-4 md:mt-auto">
              <Link to="/platform/login" className="inline-flex items-center gap-1 rounded text-xs text-muted-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <ShieldCheck size={12} aria-hidden />
                Platform administration
              </Link>
              <ThemeToggle />
            </div>
          </nav>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <main className="flex-1 p-4 md:p-8">
            <Outlet />
          </main>
          <PublicFooter />
        </div>
      </div>
    </InPublicLayout.Provider>
  )
}

function PublicFooter() {
  return (
    <footer className="border-t border-border bg-card px-4 py-6 text-sm text-muted-foreground md:px-8">
      <div className="mx-auto flex max-w-5xl flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="font-medium text-foreground">{SITE.productName}</p>
          <p>{SITE.tagline}.</p>
        </div>
        <nav aria-label="Footer" className="flex flex-wrap gap-x-4 gap-y-1">
          {PUBLIC_LINKS.map((l) => (
            <Link key={l.to} to={l.to} className="underline-offset-2 hover:underline">
              {l.label}
            </Link>
          ))}
          <Link to="/platform/login" className="underline-offset-2 hover:underline">
            Platform administration
          </Link>
        </nav>
      </div>
      <p className="mx-auto mt-4 max-w-5xl text-xs">
        Illustrations: original, made for {SITE.productName}. Icons: Lucide (ISC licence). Details:{' '}
        <a className="underline" href="/credits.txt">
          credits
        </a>
        .
      </p>
    </footer>
  )
}
