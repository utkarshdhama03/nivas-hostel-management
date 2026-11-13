// The notification bell (docs/04 §12.3, D84) for the admin area and the student portal.
//
// Before D84 the bell was only an icon with a number: nothing to click, no list, and nothing marked a
// notification read. Now:
//  * the badge shows the TRUE unread count from /me/notifications/unread-count (not the size of a
//    page), written "99+" above 99;
//  * clicking the bell opens the newest notifications (20 at a time, "Show more" for older ones);
//  * clicking one marks it read and opens its page (every link is a page that exists: the database
//    allows only those, see notification-links.test.ts); one without a link is just marked read;
//  * "Mark all as read" clears the badge.
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'

import { api } from '@/api/client'
import type { NotificationOut, NotificationPage, UnreadCount } from '@/api/types'
import { useMe } from '@/auth/session'
import { Button } from '@/components/ui/button'
import { dateIn, showDate } from '@/lib/dates'

export const PAGE_SIZE = 20

/** The badge text: the real number, or "99+" above 99. */
export function badgeText(count: number): string {
  return count > 99 ? '99+' : String(count)
}

export function NotificationBell() {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const timeZone = useMe().data?.organization.timezone ?? 'Asia/Kolkata'

  // Polled every 60 s (docs/02 §10): simpler than websockets, fresh enough.
  const unread = useQuery({ queryKey: ['unread'], queryFn: () => api<UnreadCount>('/me/notifications/unread-count'), refetchInterval: 60_000 })
  const count = unread.data?.unread ?? 0
  const list = useInfiniteQuery({
    queryKey: ['notifications'],
    queryFn: ({ pageParam }) => api<NotificationPage>(`/me/notifications?limit=${PAGE_SIZE}${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ''}`),
    initialPageParam: '',
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: open,
  })
  const items = list.data?.pages.flatMap((p) => p.items) ?? []

  const refresh = () => Promise.all([queryClient.invalidateQueries({ queryKey: ['unread'] }), queryClient.invalidateQueries({ queryKey: ['notifications'] })])
  const markAll = useMutation({ mutationFn: () => api<UnreadCount>('/me/notifications/read-all', { method: 'POST' }), onSuccess: refresh })

  async function openItem(n: NotificationOut) {
    setOpen(false)
    if (!n.read_at) {
      try {
        await api(`/me/notifications/${n.id}/read`, { method: 'POST' })
      } catch {
        // Not being able to mark it read must not stop the person reaching the page.
      }
      void refresh()
    }
    if (n.link) navigate(n.link)
  }

  useEffect(() => {
    if (!open) return
    const outside = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    const escape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        className="relative inline-flex rounded-md p-1.5 hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`Notifications: ${count} unread`}
        aria-expanded={open}
        aria-haspopup="dialog"
        data-testid="notification-bell"
        onClick={() => setOpen((o) => !o)}
      >
        <Bell size={18} />
        {count > 0 && (
          <span className="absolute -right-1.5 -top-1 rounded-full bg-destructive px-1.5 text-[10px] leading-4 text-destructive-foreground" data-testid="unread-badge">
            {badgeText(count)}
          </span>
        )}
      </button>
      {open && (
        <div role="dialog" aria-label="Notifications" className="absolute right-0 z-50 mt-2 w-[min(24rem,calc(100vw-2rem))] rounded-lg border border-border bg-card shadow-lg">
          <div className="flex items-center justify-between border-b border-border px-4 py-2">
            <p className="text-sm font-semibold">Notifications</p>
            <Button variant="ghost" size="sm" disabled={count === 0 || markAll.isPending} onClick={() => markAll.mutate()}>
              Mark all as read
            </Button>
          </div>
          <ul className="max-h-96 overflow-y-auto">
            {list.isPending && <li className="px-4 py-3 text-sm text-muted-foreground">Loading…</li>}
            {list.isError && <li className="px-4 py-3 text-sm text-destructive">Couldn't load notifications. Try again in a moment.</li>}
            {list.isSuccess && items.length === 0 && <li className="px-4 py-3 text-sm text-muted-foreground">No notifications yet.</li>}
            {items.map((n) => (
              <li key={n.id} className="border-b border-border last:border-b-0">
                <button type="button" className="block w-full px-4 py-2.5 text-left hover:bg-muted" data-unread={!n.read_at} onClick={() => void openItem(n)}>
                  <span className="flex items-start gap-2">
                    {!n.read_at && <span className="mt-1.5 size-2 shrink-0 rounded-full bg-primary" aria-label="Unread" />}
                    <span className="min-w-0">
                      <span className={`block text-sm ${n.read_at ? '' : 'font-semibold'}`}>{n.title}</span>
                      <span className="block truncate text-xs text-muted-foreground">{n.body}</span>
                      <span className="block text-xs text-muted-foreground">{showDate(dateIn(timeZone, new Date(n.created_at)))}</span>
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {list.hasNextPage && (
            <div className="border-t border-border p-2 text-center">
              <Button variant="ghost" size="sm" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
                Show more
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
