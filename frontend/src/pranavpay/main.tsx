import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router'
import { Providers } from '@/app/providers'
import { AuthSync } from '@/components/auth/AuthSync'
import { PageLoader } from '@/components/ui/page-loader'
import AccountPage from '../pages/pranavpay/pages/AccountPage'
import ManagePage from '../pages/pranavpay/pages/ManagePage'
import OverviewPage from '../pages/pranavpay/pages/OverviewPage'
import TransactionsPage from '../pages/pranavpay/pages/TransactionsPage'
import WalletPage from '../pages/pranavpay/pages/WalletPage'
import Shell from '../pages/pranavpay/Shell'
import '../pages/pranavpay/pranavpay.css'

/**
 * PranavPay: the calm, beginner-facing surface.
 *
 * Deliberately a separate entry point from the OpenAlgo app rather than a
 * rewrite of it. OpenAlgo stays at /dashboard and its own routes; this owns
 * the root and its four sections, and the only bridge between them is the
 * "Advanced view" control on the account page. Both read the same endpoints,
 * so neither can break the other.
 *
 * Sections are routes, not hidden panels, so a reload or a shared link lands
 * where the user expects.
 */
function App() {
  return (
    <StrictMode>
      <Providers>
        <BrowserRouter>
          <AuthSync>
            <Routes>
              <Route element={<Shell />}>
                <Route index element={<OverviewPage />} />
                <Route path="wallet" element={<WalletPage />} />
                <Route path="transactions" element={<TransactionsPage />} />
                <Route path="manage" element={<ManagePage />} />
                <Route path="account" element={<AccountPage />} />
              </Route>
              <Route path="*" element={<PageLoader />} />
            </Routes>
          </AuthSync>
        </BrowserRouter>
      </Providers>
    </StrictMode>
  )
}

const container = document.getElementById('pranavpay-root')
if (container) {
  createRoot(container).render(<App />)
}
