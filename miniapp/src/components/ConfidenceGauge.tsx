import type { Verdict } from '../types'
import { displayAuthenticityIndex } from '../lib/resultPresentation'

interface ConfidenceGaugeProps {
  confidence: number
  authenticityIndex?: number | null
  verdict: Verdict
}

const VERDICT_COLORS: Record<Verdict, string> = {
  REAL: '#10B981',
  FAKE: '#EF4444',
  UNCERTAIN: '#F59E0B',
}

export function ConfidenceGauge({ confidence, authenticityIndex, verdict }: ConfidenceGaugeProps) {
  const displayedIndex = displayAuthenticityIndex(authenticityIndex, confidence, verdict)
  const radius = 50
  const circumference = 2 * Math.PI * radius
  const progress = (displayedIndex / 100) * circumference
  const color = VERDICT_COLORS[verdict]

  return (
    <div className="flex flex-col items-center">
      <div className="relative w-[120px] h-[120px]">
        <svg className="w-full h-full -rotate-90" viewBox="0 0 120 120">
          {/* Background circle */}
          <circle
            cx="60"
            cy="60"
            r={radius}
            fill="none"
            stroke="#2A2A30"
            strokeWidth="8"
          />
          {/* Progress circle */}
          <circle
            cx="60"
            cy="60"
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth="8"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={circumference - progress}
            style={{ transition: 'stroke-dashoffset 0.5s ease' }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-2xl font-bold text-mv-text">{displayedIndex}%</span>
        </div>
      </div>
      <span className="text-xs text-mv-text-secondary mt-2">Индекс подлинности</span>
    </div>
  )
}
