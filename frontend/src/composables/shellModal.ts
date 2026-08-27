import type { InjectionKey, Ref } from 'vue'

export const shellModalOpenKey: InjectionKey<Ref<boolean>> = Symbol('shellModalOpen')
