import QRCode from 'qrcode'
import { useEffect, useState } from 'react'

/** Draws a QR code locally (the qrcode library); the secret never goes to any outside service. */
export function QrCode({ value, label }: { value: string; label: string }) {
  const [src, setSrc] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    void QRCode.toDataURL(value, { margin: 1, width: 220 }).then((url) => {
      if (alive) setSrc(url)
    })
    return () => {
      alive = false
    }
  }, [value])
  return src ? <img src={src} alt={label} width={220} height={220} className="rounded-lg border border-border bg-white p-2" /> : null
}
