// Shows a stored image through a 60-second signed link (04 §11.2): an <img> can't send the
// Authorization header, so the API hands out a short link bound to this user and session.
import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { FileLinkOut } from '@/api/types'

export function Photo({ fileId, alt, size = 64 }: { fileId: string; alt: string; size?: number }) {
  const link = useQuery({ queryKey: ['file-link', fileId], queryFn: () => api<FileLinkOut>(`/files/${fileId}/link`), staleTime: 45_000 })
  if (!link.data) return <div className="rounded-lg bg-muted" style={{ width: size, height: size }} />
  return <img src={link.data.url} alt={alt} width={size} height={size} className="rounded-lg object-cover" style={{ width: size, height: size }} />
}
