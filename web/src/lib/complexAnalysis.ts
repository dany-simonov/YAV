import type { User } from '../types';

export const COMPLEX_ANALYSIS_ROUTE = '/dashboard/check?tab=complex';
export const COMPLEX_MIN_LENGTH = 200;
// This matches the active new-user admission policy. The backend schema allows
// more, but requests above this limit are rejected before provider execution.
export const COMPLEX_MAX_LENGTH = 3000;
export const COMPLEX_RECOMMENDED_RANGE = { min: 200, max: 2000 };

export const isComplexTextSubmittable = (value: string, isAnalyzing: boolean): boolean =>
  value.trim().length >= COMPLEX_MIN_LENGTH && value.length <= COMPLEX_MAX_LENGTH && !isAnalyzing;

export const buildComplexPayload = (text: string, user: User) => ({
  text,
  userId: user.$id,
  username: user.name,
  firstName: user.name.split(' ')[0] || '',
  mediaType: 'text' as const,
  mode: 'hybrid_text' as const,
  sourceLabel: text.slice(0, 120).replace(/\s+/g, ' ').trim(),
});
