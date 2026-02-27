import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { DashboardPage } from '../features/dashboard/DashboardPage';
import { LoginPage } from '../features/auth/LoginPage';
import { ListeningPracticeRuntimeBridge } from '../features/listening-practice/ListeningPracticeRuntimeBridge';
import { ListeningUploadLayoutBridge } from '../features/listening-upload/ListeningUploadLayoutBridge';
import { ProfilePage } from '../features/profile/ProfilePage';
import { ReadingPage } from '../features/reading/ReadingPage';
import { WalletPage } from '../features/wallet/WalletPage';
import { fetchAuthMe } from '../lib/api/auth';
import { clearAuthToken, getAuthToken } from '../lib/api/auth-token';

function RequireAuth({ children }: { children: JSX.Element }) {
  const location = useLocation();
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const token = getAuthToken();
    if (!token) {
      setAuthed(false);
      setReady(true);
      return () => {
        cancelled = true;
      };
    }
    setReady(false);
    void fetchAuthMe()
      .then(() => {
        if (cancelled) return;
        setAuthed(true);
        setReady(true);
      })
      .catch(() => {
        clearAuthToken();
        if (cancelled) return;
        setAuthed(false);
        setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

  if (!ready) return <div />;
  if (!authed) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return children;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<RequireAuth><Navigate to="/listening" replace /></RequireAuth>} />
      <Route path="/dashboard" element={<RequireAuth><DashboardPage /></RequireAuth>} />
      <Route path="/listening" element={<RequireAuth><ListeningUploadLayoutBridge /></RequireAuth>} />
      <Route path="/listening/practice" element={<RequireAuth><ListeningPracticeRuntimeBridge /></RequireAuth>} />
      <Route path="/reading" element={<RequireAuth><ReadingPage /></RequireAuth>} />
      <Route path="/wallet" element={<RequireAuth><WalletPage /></RequireAuth>} />
      <Route path="/profile" element={<RequireAuth><ProfilePage /></RequireAuth>} />
      <Route path="/vocab" element={<RequireAuth><Navigate to="/listening" replace /></RequireAuth>} />
      <Route path="*" element={<Navigate to="/listening" replace />} />
    </Routes>
  );
}
