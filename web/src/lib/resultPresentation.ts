import type { Verdict } from '../types';

const MODEL_NAMES: Record<string, string> = {
  gemini_video_verification: 'Gemini Video Verification',
  gemini_text_verification: 'Gemini Text Verification',
  gemini_credibility: 'Gemini Credibility',
};

const clampIndex = (value: number): number => Math.max(0, Math.min(100, Math.round(value)));

/** Use the v2 canonical index whenever the backend supplied one. */
export function displayAuthenticityIndex(
  authenticityIndex: number | null | undefined,
  confidence: number | null | undefined,
  verdict: Verdict,
): number {
  if (typeof authenticityIndex === 'number' && Number.isFinite(authenticityIndex)) {
    return clampIndex(authenticityIndex);
  }

  // Legacy records did not have authenticity_index. Their confidence remains
  // interpreted with the historical UI convention only on this fallback path.
  const safeConfidence = typeof confidence === 'number' && Number.isFinite(confidence)
    ? confidence
    : 0;
  const percentage = safeConfidence <= 1 ? safeConfidence * 100 : safeConfidence;
  return clampIndex(verdict === 'FAKE' ? 100 - percentage : percentage);
}

export function displayModelName(model: string): string {
  return MODEL_NAMES[model] ?? model;
}
