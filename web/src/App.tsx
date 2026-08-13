import { useEffect } from 'react';
import { HashRouter, Routes, Route } from 'react-router-dom';
import { Header, Footer, DashboardLayout, ProtectedRoute, PublicOnlyRoute, VerifiedRoute } from './components';
import { 
  Home, 
  About, 
  FAQ, 
  Docs, 
  Privacy, 
  Terms, 
  NotFound,
  LoginPage,
  RegisterPage,
  DashboardOverview,
  NewCheckPage,
  BigTextCheckPage,
  HistoryPage,
  HistoryDetailPage,
  ApiSettingsPage,
  Research,
  ArcticResearch,
  EmailVerificationPendingPage
} from './pages';
import { useAuthStore } from './store';
import { VerifyEmailCallbackPage } from './pages/auth/VerifyEmailCallbackPage';

function AppContent() {
  const { user, isLoading, initialize, logout } = useAuthStore();

  // Initialize auth on mount
  useEffect(() => {
    initialize();
  }, [initialize]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-mv-bg">
        <div className="text-center">
          <div className="w-10 h-10 mx-auto border-2 border-mv-accent border-t-transparent rounded-full animate-spin mb-4" />
          <p className="text-mv-text-secondary">Загрузка...</p>
        </div>
      </div>
    );
  }

  return (
    <Routes>
      {/* Public Routes with Header/Footer */}
      <Route
        path="/"
        element={
          <div className="min-h-screen bg-mv-bg flex flex-col">
            <Header
              isLoggedIn={!!user}
              onLogout={logout}
            />
            <main className="flex-1">
              <Home />
            </main>
            <Footer />
          </div>
        }
      />

      {/* Public pages with Header/Footer */}
      {[
        { path: '/about', element: <About /> },
        { path: '/faq', element: <FAQ /> },
        { path: '/docs', element: <Docs /> },
        { path: '/privacy', element: <Privacy /> },
        { path: '/terms', element: <Terms /> },
        { path: '/research', element: <Research /> },
        { path: '/research/arctic', element: <ArcticResearch /> },
      ].map(({ path, element }) => (
        <Route
          key={path}
          path={path}
          element={
            <div className="min-h-screen bg-mv-bg flex flex-col">
              <Header
                isLoggedIn={!!user}
                onLogout={logout}
              />
              <main className="flex-1">{element}</main>
              <Footer />
            </div>
          }
        />
      ))}

      {/* Auth Routes (redirect if logged in) */}
      <Route
        path="/login"
        element={
          <PublicOnlyRoute>
            <LoginPage />
          </PublicOnlyRoute>
        }
      />
      <Route
        path="/register"
        element={
          <PublicOnlyRoute>
            <RegisterPage />
          </PublicOnlyRoute>
        }
      />

      {/* Appwrite email verification callback and resend screen */}
      <Route path="/verify-email" element={<EmailVerificationPendingPage />} />
      <Route path="/verify-email/pending" element={<EmailVerificationPendingPage />} />
      <Route path="/verify-email/callback" element={<VerifyEmailCallbackPage />} />

      {/* Protected Dashboard Routes */}
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute>
            <DashboardLayout />
          </ProtectedRoute>
        }
      >
        <Route index element={<DashboardOverview />} />
        <Route path="check" element={<VerifiedRoute><NewCheckPage /></VerifiedRoute>} />
        <Route path="big-text" element={<VerifiedRoute><BigTextCheckPage /></VerifiedRoute>} />
        <Route path="history" element={<VerifiedRoute><HistoryPage /></VerifiedRoute>} />
        <Route path="history/:checkId" element={<VerifiedRoute><HistoryDetailPage /></VerifiedRoute>} />
        <Route path="api" element={<ApiSettingsPage />} />
      </Route>

      {/* 404 */}
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}

export default function App() {
  return (
    <HashRouter>
      <AppContent />
    </HashRouter>
  );
}
