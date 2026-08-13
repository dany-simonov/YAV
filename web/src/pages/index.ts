/**
 * Pages Exports
 * =============
 * Централизованный экспорт всех страниц.
 */

// Public pages
export { Home } from './Home';
export { About } from './About';
export { FAQ } from './FAQ';
export { Docs } from './Docs';
export { Privacy } from './Privacy';
export { Terms } from './Terms';
export { NotFound } from './NotFound';
export { Research, ArcticResearch } from './Research';
export { Dashboard } from './Dashboard';

// Auth pages
export { LoginPage, RegisterPage, VerifyEmailPage, EmailVerificationPendingPage } from './auth';

// Dashboard pages (explicit paths to avoid casing issues)
export { DashboardOverview } from './dashboard/DashboardOverview';
export { NewCheckPage } from './dashboard/NewCheckPage';
export { BigTextCheckPage } from './dashboard/BigTextCheckPage';
export { HistoryPage } from './dashboard/HistoryPage';
export { HistoryDetailPage } from './dashboard/HistoryDetailPage';
export { ApiSettingsPage } from './dashboard/ApiSettingsPage';
