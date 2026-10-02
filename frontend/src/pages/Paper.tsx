import { useThemeStore } from '@/stores/themeStore'
import PaperTrading from './pranavpay/pages/PaperPage'
import './pranavpay/pranavpay.css'

/**
 * Paper trading inside the OpenAlgo app shell.
 *
 * One page, two shells. `PaperPage` and its `usePaperState` hook were written
 * for PranavPay and stay where they are; this wrapper is what lets the same
 * component render under the main app's navbar, so the page is not duplicated
 * and the two surfaces can never disagree about what the engine reported.
 *
 * The stylesheet is imported here rather than left to the PranavPay entry point,
 * because it is scoped under `.pp-root` and nothing reaches the page without
 * that class on an ancestor. The theme is pinned to the app's own light/dark
 * choice instead of following the device, so the panel never renders dark
 * inside a light shell (or the reverse).
 */
export default function Paper() {
  const mode = useThemeStore((state) => state.mode)

  return (
    <div className="pp-root" data-theme={mode === 'dark' ? 'dark' : 'light'}>
      <PaperTrading />
    </div>
  )
}
