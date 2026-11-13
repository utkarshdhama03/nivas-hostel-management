// The front page (/): what the product is, who it's for, how it works, how it keeps data safe, and how
// to get in touch. The words come from here; the name and contact details from config/site.ts.
import {
  BedDouble,
  ClipboardCheck,
  KeyRound,
  Mail,
  MessageSquareWarning,
  Phone,
  ReceiptIndianRupee,
  ScrollText,
  ShieldCheck,
  UtensilsCrossed,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { SITE } from '@/config/site'
import { HostelIllustration, PortalIllustration } from '@/pages/public/illustrations'

const FEATURES: { icon: ReactNode; title: string; text: string }[] = [
  { icon: <BedDouble aria-hidden />, title: 'Rooms and beds', text: 'Hostels, buildings, floors and beds; allocations, transfers and a live bed board. A bed is never booked twice.' },
  { icon: <ReceiptIndianRupee aria-hidden />, title: 'Fees and receipts', text: 'Monthly, term and annual fees, late fees, deposits and refunds. Receipts numbered without gaps, payments by cash, UPI or bank.' },
  { icon: <ClipboardCheck aria-hidden />, title: 'Leave requests', text: 'Students ask for home leave or a local outing from their phone; an administrator approves or rejects it.' },
  { icon: <MessageSquareWarning aria-hidden />, title: 'Complaints', text: 'Students raise complaints with photos; administrators assign, fix and resolve them, with a target date for each.' },
  { icon: <UtensilsCrossed aria-hidden />, title: 'Mess menu', text: 'A weekly menu per hostel, copied forward in one step, which students see on their phones.' },
]

const STEPS: { title: string; text: string }[] = [
  { title: 'The organization registers', text: 'Its administrator confirms the email address and is signed in.' },
  { title: 'It sets up hostels', text: 'Buildings, rooms and beds (or a spreadsheet import) and fees, by the organization’s administrator.' },
  { title: 'It admits students', text: 'Each student gets a bed and an activation link by email, to choose a password.' },
  { title: 'Students sign in', text: 'They see their fees, receipts and complaints, and ask for leave, from any phone.' },
]

function Section({ id, title, intro, children }: { id: string; title: string; intro?: string; children: ReactNode }) {
  return (
    <section aria-labelledby={`${id}-title`} className="space-y-6">
      <div className="max-w-2xl space-y-2">
        <h2 id={`${id}-title`} className="text-2xl font-semibold tracking-tight">
          {title}
        </h2>
        {intro && <p className="text-muted-foreground">{intro}</p>}
      </div>
      {children}
    </section>
  )
}

export function LandingPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-16 pb-8">
      {/* 1. Hero */}
      <section className="grid items-center gap-8 md:grid-cols-2" aria-labelledby="hero-title">
        <div className="space-y-5">
          <p className="text-sm font-semibold uppercase tracking-wider text-primary">{SITE.productName}</p>
          <h1 id="hero-title" className="text-3xl font-bold tracking-tight md:text-4xl">
            {SITE.tagline}
          </h1>
          <p className="text-lg text-muted-foreground">
            Beds, fees, payments, leave, complaints and the mess menu in one place: for the hostel office and every student.
          </p>
          <div className="flex flex-wrap gap-3">
            <Button asChild size="lg">
              <Link to="/register">Register your organization</Link>
            </Button>
            <Button asChild size="lg" variant="outline">
              <Link to="/sign-in/student">Student sign-in</Link>
            </Button>
          </div>
        </div>
        <HostelIllustration className="w-full" />
      </section>

      {/* 2. What it does */}
      <Section id="features" title="What it does" intro="Everything a hostel office does every day, without paper registers or spreadsheets.">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f) => (
            <Card key={f.title} className="space-y-2">
              <div className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">{f.icon}</div>
              <h3 className="font-semibold">{f.title}</h3>
              <p className="text-sm text-muted-foreground">{f.text}</p>
            </Card>
          ))}
        </div>
      </Section>

      {/* 3. Who it's for */}
      <Section id="audience" title="Who it's for">
        <div className="grid gap-4 md:grid-cols-[1fr_1fr_auto] md:items-stretch">
          <Card className="space-y-3">
            <h3 className="font-semibold">Organizations</h3>
            <p className="text-sm text-muted-foreground">Colleges, universities and private hostel operators, with one or many hostels.</p>
            <p className="text-sm" data-testid="audience-organizations">
              One administrator manages hostels, students, fees, payments, leave and complaints.
            </p>
          </Card>
          <Card className="space-y-3">
            <h3 className="font-semibold">Students</h3>
            <p className="text-sm text-muted-foreground">Everything about their stay, on their own phone.</p>
            <ul className="list-disc space-y-1 pl-5 text-sm">
              <li>Fees due, receipts, and paying by UPI</li>
              <li>Leave requests and the answers</li>
              <li>Complaints, the mess menu and announcements</li>
            </ul>
          </Card>
          <PortalIllustration className="hidden w-48 md:block" />
        </div>
      </Section>

      {/* 4. How it works */}
      <Section id="how" title="How it works">
        <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s, i) => (
            <li key={s.title}>
              <Card className="h-full space-y-2">
                <span aria-hidden className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-primary text-sm font-bold text-primary-foreground">
                  {i + 1}
                </span>
                <h3 className="font-semibold">{s.title}</h3>
                <p className="text-sm text-muted-foreground">{s.text}</p>
              </Card>
            </li>
          ))}
        </ol>
      </Section>

      {/* 5. Security and privacy */}
      <Section id="security" title="Security and privacy">
        <div className="grid gap-4 md:grid-cols-3">
          <Card className="space-y-2">
            <ShieldCheck aria-hidden className="text-primary" />
            <h3 className="font-semibold">Each organization&apos;s data is separate</h3>
            <p className="text-sm text-muted-foreground">
              One organization can never see another&apos;s students, money or documents. The database itself enforces it, not only the
              screens.
            </p>
          </Card>
          <Card className="space-y-2">
            <KeyRound aria-hidden className="text-primary" />
            <h3 className="font-semibold">Passwords kept safe</h3>
            <p className="text-sm text-muted-foreground">
              Passwords are stored only as slow one-way hashes, and repeated wrong tries lock the account for a while. A
              forgotten password is reset with a 6-digit code sent by email.
            </p>
          </Card>
          <Card className="space-y-2">
            <ScrollText aria-hidden className="text-primary" />
            <h3 className="font-semibold">An audit log</h3>
            <p className="text-sm text-muted-foreground">
              Every change to money, rooms, students and permissions is recorded with who made it and when, and can&apos;t be edited.
            </p>
          </Card>
        </div>
      </Section>

      {/* 6. Contact */}
      <Section id="contact" title="Contact" intro="Questions, a demonstration for your college, or help getting started.">
        <Card className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-8">
          <a className="inline-flex items-center gap-2 text-primary underline-offset-2 hover:underline" href={`mailto:${SITE.contact.email}`}>
            <Mail size={18} aria-hidden />
            {SITE.contact.email}
          </a>
          <a className="inline-flex items-center gap-2 text-primary underline-offset-2 hover:underline" href={`tel:${SITE.contact.phone.replace(/\s/g, '')}`}>
            <Phone size={18} aria-hidden />
            {SITE.contact.phone}
          </a>
          <p className="text-sm text-muted-foreground">{SITE.contact.hours}</p>
        </Card>
      </Section>
    </div>
  )
}
