import type { Verdict } from '../types'

const clampIndex = (value: number): number => Math.max(0, Math.min(100, Math.round(value)))

export function displayAuthenticityIndex(
  authenticityIndex: number | null | undefined,
  confidence: number,
  verdict: Verdict,
): number {
  if (typeof authenticityIndex === 'number' && Number.isFinite(authenticityIndex)) {
    return clampIndex(authenticityIndex)
  }

  const percentage = confidence <= 1 ? confidence * 100 : confidence
  return clampIndex(verdict === 'FAKE' ? 100 - percentage : percentage)
}

export function displayModelName(model: string): string {
  return model === 'gemini_video_verification' ? 'Gemini Video Verification' : model
}
