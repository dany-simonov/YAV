import { useState } from 'react'
import type { Check, MediaType } from '../types'
import { VerdictBadge } from './VerdictBadge'
import { ConfidenceGauge } from './ConfidenceGauge'

interface CheckCardProps {
  check: Check
}

const MEDIA_ICONS: Record<MediaType, string> = {
  image: '🖼',
  audio: '🎵',
  video: '🎥',
  text: '📝',
}

const MEDIA_LABELS: Record<MediaType, string> = {
  image: 'Изображение',
  audio: 'Аудио',
  video: 'Видео',
  text: 'Текст',
}

const modelName = (value: string): string =>
  value === 'gemini_video_verification' ? 'Gemini Video Verification' : value

function formatDate(dateString: string): string {
  const date = new Date(dateString)
  return date.toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function CheckCard({ check }: CheckCardProps) {
  const [expanded, setExpanded] = useState(false)
  const confidencePercent = check.confidence <= 1 ? Math.round(check.confidence * 100) : Math.round(check.confidence)

  return (
    <div
      className="bg-mv-card rounded-2xl overflow-hidden transition-all duration-300"
      onClick={() => setExpanded(!expanded)}
    >
      {/* Header */}
      <div className="flex items-center justify-between p-4 cursor-pointer">
        <div className="flex items-center gap-3">
          <span className="text-2xl">{MEDIA_ICONS[check.media_type]}</span>
          <div>
            <p className="text-mv-text font-medium">{MEDIA_LABELS[check.media_type]}</p>
            <p className="text-mv-text-secondary text-xs">{formatDate(check.created_at)}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <VerdictBadge verdict={check.verdict} />
          <span
            className={`text-mv-text-secondary transition-transform duration-300 ${
              expanded ? 'rotate-180' : ''
            }`}
          >
            ▼
          </span>
        </div>
      </div>

      {/* Expanded content */}
      <div
        className={`transition-all duration-300 overflow-hidden ${
          expanded ? 'max-h-96 opacity-100' : 'max-h-0 opacity-0'
        }`}
      >
        <div className="px-4 pb-4 pt-2 border-t border-white/5">
          <div className="flex items-center gap-6">
            <ConfidenceGauge value={confidencePercent} authenticityIndex={check.authenticity_index} verdict={check.verdict} />
            <div className="flex-1 space-y-2">
              <div className="text-sm">
                <span className="text-mv-text-secondary">Модель: </span>
                <span className="text-mv-text">{modelName(check.model_used)}</span>
              </div>
              <div className="text-sm">
                <span className="text-mv-text-secondary">Время: </span>
                <span className="text-mv-text">{check.processing_ms} мс</span>
              </div>
            </div>
          </div>
          {check.explanation && (
            <p className="mt-4 text-sm text-mv-text-secondary">{check.explanation}</p>
          )}
        </div>
      </div>
    </div>
  )
}
