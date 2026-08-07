import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

// A fresh, retry-free client per call so tests don't wait out react-query's
// default exponential backoff on an expected error, and don't leak cached
// data between tests the way the shared app-wide queryClient intentionally
// does in production.
export function withQueryClient(node: ReactNode): ReactNode {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{node}</QueryClientProvider>
}
