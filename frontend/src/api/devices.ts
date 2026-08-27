import http from './http'

export interface DeviceInfo {
  model_id: string
  model_name: string
  category: string
  specs: string
  description: string
  price: number
  features: string
  total_stock: number
}

export function searchDevices(keyword: string) {
  return http.get<DeviceInfo[]>('/devices', { params: { keyword } }).then((r) => r.data)
}

export function deviceDetail(modelId: string) {
  return http.get<DeviceInfo>(`/devices/${encodeURIComponent(modelId)}`).then((r) => r.data)
}

export function compareDevices(modelIds: string[]) {
  return http.post<DeviceInfo[]>('/devices/compare', { model_ids: modelIds }).then((r) => r.data)
}

export function restockNotify(modelId: string) {
  return http
    .post<{ id: number; account_id: string; model_id: string; status: string }>(
      `/devices/restock-notify/${encodeURIComponent(modelId)}`,
    )
    .then((r) => r.data)
}
