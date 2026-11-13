// Helpers for the end-to-end flows: read emails from Mailpit, set organizations up through the API.
import { crc32, deflateSync } from 'node:zlib'

import type { APIRequestContext } from '@playwright/test'

const MAILPIT = process.env.MAILPIT_API ?? 'http://mailpit:8025/api/v1'

type Message = { ID: string; Subject: string }

/** The newest email to `to` (optionally: whose subject contains `subject`), waiting for it. */
export async function latestEmailText(to: string, subject = '', timeoutMs = 15_000): Promise<string> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const res = await fetch(`${MAILPIT}/search?query=${encodeURIComponent(`to:"${to}"`)}`)
    const { messages } = (await res.json()) as { messages: Message[] }
    const first = messages.find((m) => m.Subject.includes(subject))
    if (first) {
      const full = (await (await fetch(`${MAILPIT}/message/${first.ID}`)).json()) as { Text: string }
      return full.Text
    }
    await new Promise((r) => setTimeout(r, 500)) // the worker sends within about a second
  }
  throw new Error(`no email for ${to}`)
}

export function linkIn(text: string): string {
  const match = text.match(/https?:\/\/\S+#token=[A-Za-z0-9_-]+/)
  if (!match) throw new Error('no link in email')
  const url = new URL(match[0])
  return url.pathname + url.hash
}

export const unique = () => Math.random().toString(36).slice(2, 8)

// --- organization set-up through the API (the UI path is covered by Demo 0) -----------------------


export type OrgSetup = { slug: string; email: string; password: string; token: string }

export async function registerOrg(request: APIRequestContext, name: string): Promise<OrgSetup> {
  const id = unique()
  const slug = `${name}-${id}`
  const email = `owner.${id}@example.com`
  const password = 'violet tram 4 eleven'
  const reg = await request.post('/api/v1/public/organizations', {
    data: { organization: { name: `${name} Hostels`, slug, contact_email: `office.${id}@example.com` }, super_admin: { full_name: 'Asha Rao', email, password } },
  })
  if (reg.status() !== 202) throw new Error(`registration failed: ${reg.status()} ${await reg.text()}`)
  const token = linkIn(await latestEmailText(email, 'Confirm your organization')).split('#token=')[1]
  // Confirming the email signs the administrator in (D87: no authenticator).
  const done = await (await request.post(`/api/v1/o/${slug}/auth/verify-registration`, { data: { token } })).json()
  return { slug, email, password, token: done.access_token as string }
}

export function bearer(token: string) {
  return { Authorization: `Bearer ${token}` }
}

/** A real 8x8 PNG built by hand (signature, IHDR, IDAT, IEND), so tests need no image files. */
export function pngBuffer(): Buffer {
  const chunk = (type: string, data: Buffer) => {
    const len = Buffer.alloc(4)
    len.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(type), data])
    const crc = Buffer.alloc(4)
    crc.writeUInt32BE(crc32(body) >>> 0)
    return Buffer.concat([len, body, crc])
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(8, 0)
  ihdr.writeUInt32BE(8, 4)
  ihdr[8] = 8 // bit depth
  ihdr[9] = 2 // RGB
  const rows = Buffer.concat(Array.from({ length: 8 }, () => Buffer.from([0, ...Array(8 * 3).fill(120)])))
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(rows)), chunk('IEND', Buffer.alloc(0))])
}

export function pdfBuffer(): Buffer {
  return Buffer.from('%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n% XXXXXXXX1234 masked aadhaar\n%%EOF\n')
}

/** D80: a student's activation link comes only by email. Asks for the email again and returns the token. */
export async function emailedActivationToken(request: APIRequestContext, adminToken: string, userId: string, email: string): Promise<string> {
  const r = await request.post(`/api/v1/admin/users/${userId}/activation-email`, { headers: bearer(adminToken) })
  if (r.status() !== 202) throw new Error(`activation-email: ${r.status()} ${await r.text()}`)
  return linkIn(await latestEmailText(email, 'Activate your account')).split('#token=')[1]
}
