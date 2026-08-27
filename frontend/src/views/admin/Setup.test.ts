import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import Setup from './Setup.vue'

const createEndpoint = vi.fn().mockResolvedValue({ id: 'e1', display_name: 'Gateway', has_api_key: true, masked_api_key: '****9999' })
const runCapabilityTest = vi.fn().mockResolvedValue({ passed: true, capabilities: { generation: 'passed', streaming: 'passed' }, error_code: null })

vi.mock('../../api/models', () => ({
  createEndpoint: (...args: unknown[]) => createEndpoint(...args),
  createModel: vi.fn().mockResolvedValue({ id: 'm1' }),
  runCapabilityTest: (...args: unknown[]) => runCapabilityTest(...args),
  bindPurpose: vi.fn().mockResolvedValue({ purpose: 'primary_chat' }),
  fetchEndpoints: vi.fn().mockResolvedValue([]),
  fetchModels: vi.fn().mockResolvedValue([]),
}))
vi.mock('../../api/setup', () => ({
  fetchSetupStatus: vi.fn().mockResolvedValue({ ready: false, master_key_valid: true, required_purposes: ['primary_chat', 'embedding'], configured_purposes: [], missing_purposes: ['primary_chat', 'embedding'], can_configure: true, error_code: 'MODEL_NOT_CONFIGURED' }),
}))

describe('model initialization wizard', () => {
  it('configures an endpoint without ever rendering the submitted key again', async () => {
    const wrapper = mount(Setup)
    await flushPromises()
    await wrapper.get('input[name="display_name"]').setValue('Gateway')
    await wrapper.get('input[name="base_url"]').setValue('https://models.example/v1')
    await wrapper.get('input[name="api_key"]').setValue('sk-api-9999')
    await wrapper.get('form[data-step="endpoint"]').trigger('submit')
    await flushPromises()

    expect(createEndpoint).toHaveBeenCalledWith(expect.objectContaining({ api_key: 'sk-api-9999' }))
    expect(wrapper.text()).toContain('****9999')
    expect(wrapper.text()).not.toContain('sk-api-9999')
    expect(wrapper.text()).toContain('创建模型档案')
  })
})
