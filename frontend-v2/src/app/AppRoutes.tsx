import { Navigate, Route, Routes } from 'react-router-dom';
import { DashboardPage } from '../features/dashboard/DashboardPage';
import { ListeningPracticeRuntimeBridge } from '../features/listening-practice/ListeningPracticeRuntimeBridge';
import { ListeningUploadLayoutBridge } from '../features/listening-upload/ListeningUploadLayoutBridge';
import { ProfilePage } from '../features/profile/ProfilePage';
import { ReadingPage } from '../features/reading/ReadingPage';

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/listening" replace />} />
      <Route path="/dashboard" element={<DashboardPage />} />
      <Route path="/listening" element={<ListeningUploadLayoutBridge />} />
      <Route path="/listening/practice" element={<ListeningPracticeRuntimeBridge />} />
      <Route path="/reading" element={<ReadingPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      <Route path="/vocab" element={<Navigate to="/listening" replace />} />
      <Route path="*" element={<Navigate to="/listening" replace />} />
    </Routes>
  );
}
