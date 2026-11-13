// Sessions on the Security page are named "Chrome on Windows", not by the raw user-agent string.
import { expect, test } from 'vitest'

import { deviceName } from './app-pages'

test('a user-agent string in words', () => {
  expect(deviceName('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36')).toBe('Chrome on Windows')
  expect(deviceName('Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0 Mobile Safari/537.36')).toBe('Chrome on Android')
  expect(deviceName('Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1')).toBe('Safari on iPhone or iPad')
  expect(deviceName('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0')).toBe('Edge on Windows')
  expect(deviceName('Mozilla/5.0 (Macintosh; Intel Mac OS X 15.0; rv:140.0) Gecko/20100101 Firefox/140.0')).toBe('Firefox on Mac')
  expect(deviceName('python-httpx/0.28')).toBe('Unknown device')
  expect(deviceName(null)).toBe('Unknown device')
})
