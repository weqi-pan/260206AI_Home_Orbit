import { describe, expect, it } from 'vitest'
import { extractErrorMessage } from './http'

describe('extractErrorMessage', () => {
  it('优先展示后端给用户看的中文 message', () => {
    const body = { detail: { code: 'HOUSEHOLD_NOT_FOUND', message: '家庭不存在' } }

    expect(extractErrorMessage(body, '兜底')).toBe('家庭不存在')
  })

  it('没有 message 时退回稳定错误码', () => {
    expect(extractErrorMessage({ detail: { code: 'HOUSEHOLD_QUOTA_NOT_CONFIGURED' } }, '兜底')).toBe(
      'HOUSEHOLD_QUOTA_NOT_CONFIGURED',
    )
  })

  it('校验失败的 fields 比通用文案更能说明哪个字段错了', () => {
    const body = {
      detail: {
        code: 'VALIDATION_FAILED',
        message: '请求参数校验失败',
        fields: [
          { path: 'body.username', message: '字段必填' },
          { path: 'body.password', message: '长度不足' },
        ],
      },
    }

    expect(extractErrorMessage(body, '兜底')).toBe('字段必填; 长度不足')
  })

  it('保留旧的字符串 detail 与 FastAPI 数组形态', () => {
    expect(extractErrorMessage({ detail: '用户名或密码错误' }, '兜底')).toBe('用户名或密码错误')
    expect(extractErrorMessage({ detail: [{ loc: ['body', 'message'], msg: 'Field required' }] }, '兜底')).toBe(
      'Field required',
    )
  })

  it('无法识别的响应体回退到默认文案', () => {
    expect(extractErrorMessage({ error: { reason: 'unknown' } }, '请求失败')).toBe('请求失败')
    expect(extractErrorMessage(undefined, '请求失败')).toBe('请求失败')
  })
})
