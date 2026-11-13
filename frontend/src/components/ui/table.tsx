import type { ComponentProps, ReactNode } from 'react'

import { cn } from '@/lib/utils'

export function Table({ className, ...props }: ComponentProps<'table'>) {
  return (
    <div className="relative overflow-x-auto rounded-lg border border-border bg-card">
      <table className={cn('w-full text-left text-sm', className)} {...props} />
    </div>
  )
}

export function Th({ className, ...props }: ComponentProps<'th'>) {
  return <th className={cn('border-b border-border bg-muted px-3 py-2 font-medium', className)} {...props} />
}

export function Td({ className, ...props }: ComponentProps<'td'>) {
  return <td className={cn('border-b border-border px-3 py-2 align-top', className)} {...props} />
}

export function PageHeader({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
      <h1 className="text-2xl font-semibold">{title}</h1>
      {actions}
    </div>
  )
}

export function Select({ className, ...props }: ComponentProps<'select'>) {
  return (
    <select
      className={cn('flex h-10 w-full rounded-lg border border-input bg-card px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', className)}
      {...props}
    />
  )
}
