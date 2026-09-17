interface BarData {
  label: string
  heightPct: number // 10 to 100
  isActive?: boolean
}

const mockMonths: BarData[] = [
  { label: 'Jan', heightPct: 65 },
  { label: 'Feb', heightPct: 40 },
  { label: 'Mar', heightPct: 50, isActive: true },
  { label: 'Apr', heightPct: 85 },
  { label: 'May', heightPct: 95 },
  { label: 'Jun', heightPct: 75 },
  { label: 'Jul', heightPct: 35 },
]

export function MonetraBarChart({ data = mockMonths }: { data?: BarData[] }) {
  return (
    <div className="w-full pt-4 pb-2">
      <div className="flex items-end justify-between gap-2 sm:gap-4 h-32 px-2">
        {data.map((bar, i) => (
          <div key={bar.label || i} className="flex-1 flex flex-col items-center gap-2.5 h-full justify-end">
            <div
              className={`w-full max-w-[48px] rounded-2xl transition-all duration-300 ${
                bar.isActive
                  ? 'monetra-bar-active'
                  : 'monetra-bar-inactive opacity-80 hover:opacity-100'
              }`}
              style={{ height: `${bar.heightPct}%` }}
            />
            <span
              className={`text-[11px] font-medium tracking-tight ${
                bar.isActive
                  ? 'text-foreground font-bold'
                  : 'text-muted-foreground'
              }`}
            >
              {bar.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
