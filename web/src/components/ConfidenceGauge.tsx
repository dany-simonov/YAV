import type { Verdict } from '../types';
import { displayAuthenticityIndex } from '../lib/resultPresentation';

interface ConfidenceGaugeProps {
  confidence: number;
  authenticityIndex?: number | null;
  verdict: Verdict;
  size?: number;
}

export function ConfidenceGauge({ confidence, authenticityIndex, verdict, size = 120 }: ConfidenceGaugeProps) {
  const safePercentage = displayAuthenticityIndex(authenticityIndex, confidence, verdict);
  const displayValue = safePercentage / 100;
  
  const radius = (size - 12) / 2;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference * (1 - displayValue);

  const colors: Record<Verdict, string> = {
    REAL: '#10B981',
    FAKE: '#EF4444',
    UNCERTAIN: '#F59E0B',
  };

  const color = colors[verdict];

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="transform -rotate-90"
      >
        {/* Background circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="#2A2A30"
          strokeWidth="8"
        />
        {/* Progress circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={strokeDashoffset}
          style={{ transition: 'stroke-dashoffset 0.5s ease' }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-bold" style={{ color }}>
          {safePercentage}%
        </span>
        <span className="text-xs text-mv-text-secondary">Индекс</span>
      </div>
    </div>
  );
}
