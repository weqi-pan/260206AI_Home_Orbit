import http from '../api/http'
import type {
  MarketEntryDetail,
  MarketEntrySummary,
  MarketFavoriteSummary,
  MarketInstallResult,
  MarketListings,
  MyMarketInstall,
} from './types'

export const MARKET_CATEGORIES = ['生活', '烹饪', '家务', '设备', '知识', '效率', '其他']

export const fetchListings = (query = '', category = '') =>
  http
    .get<MarketListings>('/market/listings', { params: { query: query || undefined, category: category || undefined } })
    .then((response) => response.data)

export const fetchEntry = (entryId: string) =>
  http.get<MarketEntryDetail>(`/market/entries/${entryId}`).then((response) => response.data)

export const installEntry = (entryId: string, grantedToolKeys: string[]) =>
  http
    .post<MarketInstallResult>(`/market/entries/${entryId}/install`, { granted_tool_keys: grantedToolKeys })
    .then((response) => response.data)

export const fetchMyInstalls = () => http.get<MyMarketInstall[]>('/market/my').then((response) => response.data)

export const upgradeMarketInstall = (installationId: string, grantedToolKeys: string[]) =>
  http
    .post<{
      installation_id: string
      pinned_version: number
      granted_tool_keys: string[]
      newly_granted_tool_keys: string[]
    }>(`/market/installations/${installationId}/upgrade`, { granted_tool_keys: grantedToolKeys })
    .then((response) => response.data)

export const uninstallMarketInstall = (installationId: string, revokePermissions: boolean) =>
  http.delete(`/market/installations/${installationId}`, { params: { revoke_permissions: revokePermissions } })

export const favoriteEntry = (entryId: string) =>
  http
    .post<{ entry_id: string; favorited: boolean }>(`/market/entries/${entryId}/favorite`)
    .then((response) => response.data)

export const unfavoriteEntry = (entryId: string) =>
  http
    .delete<{ entry_id: string; favorited: boolean }>(`/market/entries/${entryId}/favorite`)
    .then((response) => response.data)

export const fetchFavorites = () =>
  http.get<MarketFavoriteSummary[]>('/market/favorites').then((response) => response.data)

export const reportEntry = (entryId: string, reason: string) =>
  http
    .post<{ id: string; entry_id: string; status: string; reason: string }>(`/market/entries/${entryId}/reports`, {
      reason,
    })
    .then((response) => response.data)

export type { MarketEntrySummary }
