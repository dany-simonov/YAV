/**
 * App Root Component
 * ==================
 * Основной компонент приложения с роутингом.
 */

import { useEffect, useState } from 'react';
import { HashRouter, Routes, Route } from 'react-router-dom';
import {
  Header,
  Footer,
  AuthModal,
  DashboardLayout,
  ProtectedRoute,
  PublicOnlyRoute,
  VerificationRoute,
} from './components';
import { 
  Home, 
  About, 
  FAQ, 
  Docs, 
  ProductHistory,
  Privacy, 
  Terms, 
  NotFound,
  LoginPage,
  RegisterPage,
  VerifyEmailPage,
  VerifyEmailCallbackPage,
  DashboardOverview,
  NewCheckPage,
  BigTextCheckPage,
  HistoryPage,
  ApiSettingsPage
} from './pages';
import { useAuthStore } from './store';

function AppContent() {
  const [authModalOpen, setAuthModalOpen] = useState(false);
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
              onLoginClick={() => setAuthModalOpen(true)}
              isLoggedIn={!!user}
              onLogout={logout}
            />
            <main className="flex-1">
              <Home />
            </main>
            <Footer />
            <AuthModal
              isOpen={authModalOpen}
              onClose={() => setAuthModalOpen(false)}
            />
          </div>
        }
      />

      {/* Public pages with Header/Footer */}
      {[
        { path: '/about', element: <About /> },
        { path: '/faq', element: <FAQ /> },
        { path: '/docs', element: <Docs /> },
        { path: '/history', element: <ProductHistory /> },
        { path: '/privacy', element: <Privacy /> },
        { path: '/terms', element: <Terms /> },
      ].map(({ path, element }) => (
        <Route
          key={path}
          path={path}
          element={
            <div className="min-h-screen bg-mv-bg flex flex-col">
              <Header
                onLoginClick={() => setAuthModalOpen(true)}
                isLoggedIn={!!user}
                onLogout={logout}
              />
              <main className="flex-1">{element}</main>
              <Footer />
              <AuthModal
                isOpen={authModalOpen}
                onClose={() => setAuthModalOpen(false)}
              />
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

      <Route
        path="/verify-email"
        element={
          <VerificationRoute>
            <VerifyEmailPage />
          </VerificationRoute>
        }
      />
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
        <Route path="check" element={<NewCheckPage />} />
        <Route path="big-text" element={<BigTextCheckPage />} />
        <Route path="history" element={<HistoryPage />} />
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
