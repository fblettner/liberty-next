// The installation's HTTP server pools (a `http://` / `https://` pool), asked once per page load:
// what an API connector — and each of its environments — names as its server instead of carrying
// an address, an account and secrets of its own. Who may not read the pools still types the name.
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { poolsOfKind } from '../../services/poolUrl'
import type { SearchSelectOption } from '../../common'

let asked: Promise<SearchSelectOption[]> | null = null
const serverPools = (): Promise<SearchSelectOption[]> => {
  asked ??= api.get<{ pools: Record<string, { url?: string }> }>('/admin/config/pools')
    .then((d) => poolsOfKind(d.pools, 'http'))
    .catch(() => [])
  return asked
}

export function useServerPools(): SearchSelectOption[] {
  const [options, setOptions] = useState<SearchSelectOption[]>([])
  useEffect(() => { let on = true; void serverPools().then((o) => { if (on) setOptions(o) }); return () => { on = false } }, [])
  return options
}
