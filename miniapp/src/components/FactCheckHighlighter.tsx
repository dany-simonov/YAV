import React from 'react'
import { HybridToken } from '../types'

const safeExternalUrl = (value: unknown): string | null => {
  if (typeof value !== 'string' || value.length > 2048) return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && url.hostname && !url.username && !url.password
      ? url.toString()
      : null
  } catch {
    return null
  }
}

interface Props {
  tokens: HybridToken[]
}

export const FactCheckHighlighter: React.FC<Props> = ({ tokens }) => {
  return (
    <div className="flex flex-wrap gap-1 leading-relaxed text-base text-mv-text">
      {tokens.map((token, idx) => {
        const isFlagged = token.type !== 'normal'
        return (
          <span
            key={`${idx}-${token.text.slice(0, 8)}`}
            className={`relative group ${isFlagged ? 'bg-red-100 text-red-700 px-1 rounded' : ''}`}
          >
            {token.text}
            {isFlagged && (
              <div className="absolute z-20 hidden w-64 max-w-sm rounded border border-red-200 bg-white p-3 text-sm shadow-lg group-hover:block top-full left-0 mt-1">
                <div className="font-semibold text-red-700 mb-1">
                  {token.type === 'fake' ? 'Фейк' : 'Манипуляция'}
                </div>
                <div className="text-gray-800 mb-1">{token.details?.truth}</div>
                {safeExternalUrl(token.details?.source_url) && (
                  <a
                    href={safeExternalUrl(token.details?.source_url) || undefined}
                    target="_blank"
                    rel="noreferrer"
                    className="text-blue-600 underline"
                  >
                    Источник
                  </a>
                )}
              </div>
            )}
          </span>
        )
      })}
    </div>
  )
}
