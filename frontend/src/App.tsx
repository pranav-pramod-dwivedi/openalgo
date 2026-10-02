import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router'
import { Providers } from '@/app/providers'
import { AuthSync } from '@/components/auth/AuthSync'
import { FullWidthLayout } from '@/components/layout/FullWidthLayout'
import { Layout } from '@/components/layout/Layout'
import { PageLoader } from '@/components/ui/page-loader'
import { usePageTitle } from '@/hooks/usePageTitle'

// Kiosk build: only Dashboard + Trading core + AI Agent survive. Every other
// surface was stripped. Legacy public/auth paths redirect to /dashboard
// (kiosk auto-login owns the session server-side); unknown paths land on
// NotFound. /apikey stays because the /trading chart loads its websocket
// key through the API-key store.
const Dashboard = lazy(() => import('@/pages/Dashboard'))
const Positions = lazy(() => import('@/pages/Positions'))
const OrderBook = lazy(() => import('@/pages/OrderBook'))
const TradeBook = lazy(() => import('@/pages/TradeBook'))
const ApiKey = lazy(() => import('@/pages/ApiKey'))
const Trading = lazy(() => import('@/pages/Trading'))
const AgentIndex = lazy(() => import('@/pages/agent/AgentIndex'))
const AgentConfig = lazy(() => import('@/pages/agent/AgentConfig'))
const NotFound = lazy(() => import('@/pages/NotFound'))

function PageTitleUpdater() {
  usePageTitle()
  return null
}

function App() {
  return (
    <Providers>
      <BrowserRouter>
        <PageTitleUpdater />
        <AuthSync>
          <Suspense fallback={<PageLoader />}>
            <Routes>
              {/* Legacy entry points fold into the dashboard */}
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/login" element={<Navigate to="/dashboard" replace />} />
              <Route path="/broker" element={<Navigate to="/dashboard" replace />} />
              <Route path="/setup" element={<Navigate to="/dashboard" replace />} />

              {/* Protected routes - kiosk session, Binance only */}
              <Route element={<Layout />}>
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/positions" element={<Positions />} />
                <Route path="/orderbook" element={<OrderBook />} />
                <Route path="/tradebook" element={<TradeBook />} />
                <Route path="/apikey" element={<ApiKey />} />
              </Route>

              {/* Full-width protected routes */}
              <Route element={<FullWidthLayout />}>
                <Route path="/trading" element={<Trading />} />
                <Route path="/agent" element={<AgentIndex />} />
                <Route path="/agent/config" element={<AgentConfig />} />
              </Route>

              {/* 404 Not Found */}
              <Route path="*" element={<NotFound />} />
            </Routes>
          </Suspense>
        </AuthSync>
      </BrowserRouter>
    </Providers>
  )
}

export default App
