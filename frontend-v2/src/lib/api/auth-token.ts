const AUTH_TOKEN_KEY = 'authAccessTokenV1';

function canUseStorage() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

export function getAuthToken(): string {
  if (!canUseStorage()) return '';
  try {
    return String(window.localStorage.getItem(AUTH_TOKEN_KEY) || '').trim();
  } catch {
    return '';
  }
}

export function setAuthToken(token: string) {
  if (!canUseStorage()) return;
  const safe = String(token || '').trim();
  try {
    if (!safe) {
      window.localStorage.removeItem(AUTH_TOKEN_KEY);
      return;
    }
    window.localStorage.setItem(AUTH_TOKEN_KEY, safe);
  } catch {
    // ignore storage failures
  }
}

export function clearAuthToken() {
  if (!canUseStorage()) return;
  try {
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
  } catch {
    // ignore storage failures
  }
}

