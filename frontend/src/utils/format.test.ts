import { describe, expect, it } from 'vitest'
import { parseServerTime } from './format'

describe('parseServerTime', () => {
  it('把不带时区的后端时间戳按 UTC 解释', () => {
    // 后端存的是 datetime.now(timezone.utc).replace(tzinfo=None)，序列化后没有 Z。
    // 直接 Date.parse 会按**本地时间**理解，在 UTC+8 上凭空多出 8 小时。
    const parsed = parseServerTime('2026-10-26T10:35:00')

    expect(parsed).toBe(Date.UTC(2026, 9, 26, 10, 35, 0))
  })

  it('已经带时区后缀的不再补 Z', () => {
    expect(parseServerTime('2026-10-26T10:35:00Z')).toBe(Date.UTC(2026, 9, 26, 10, 35, 0))
    expect(parseServerTime('2026-10-26T18:35:00+08:00')).toBe(Date.UTC(2026, 9, 26, 10, 35, 0))
  })

  it('空值与非法值返回 null', () => {
    expect(parseServerTime(null)).toBeNull()
    expect(parseServerTime('')).toBeNull()
    expect(parseServerTime('not-a-date')).toBeNull()
  })
})
