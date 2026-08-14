import { Navigate } from 'react-router-dom';

import { COMPLEX_ANALYSIS_ROUTE } from '../../lib/complexAnalysis';

/** Backward-compatible alias for old shared links. */
export const BIG_TEXT_REDIRECT_TARGET = COMPLEX_ANALYSIS_ROUTE;

export function BigTextCheckPage() {
  return <Navigate replace to={BIG_TEXT_REDIRECT_TARGET} />;
}
