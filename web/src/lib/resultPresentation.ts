import type { Verdict } from '../types';

const MODEL_NAMES: Record<string, string> = {
  gemini_video_verification: 'Gemini Video Verification',
};

const clampIndex = (value: number): number => Math.max(0, Math.min(100, Math.round(value)));

/** Use the v2 canonical index whenever the backend supplied one. */
export function displayAuthenticityIndex(
  authenticityIndex: number | null | undefined,
  confidence: number,
  verdict: Verdict,
): number {
  if (typeof authenticityIndex === 'number' && Number.isFinite(authenticityIndex)) {
    return clampIndex(authenticityIndex);
  }

  // Legacy records did not have authenticity_index. Their confidence remains
  // interpreted with the historical UI convention only on this fallback path.
  const percentage = confidence <= 1 ? confidence * 100 : confidence;
  return clampIndex(verdict === 'FAKE' ? 100 - percentage : percentage);
}

export function displayModelName(model: string): string {
  return MODEL_NAMES[model] ?? model;
}
