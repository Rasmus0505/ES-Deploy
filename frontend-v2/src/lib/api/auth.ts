import type {
  AuthLoginRequest,
  AuthLogoutResponse,
  AuthRegisterRequest,
  AuthTokenResponse,
  AuthUserResponse
} from '../../types/backend';
import { clearAuthToken, setAuthToken } from './auth-token';
import { requestJson } from './http';

export async function registerAuth(payload: AuthRegisterRequest) {
  const tokenPayload = await requestJson<AuthTokenResponse>('/auth/register', {
    method: 'POST',
    body: payload,
    retry: 0
  });
  setAuthToken(tokenPayload.access_token);
  return tokenPayload;
}

export async function loginAuth(payload: AuthLoginRequest) {
  const tokenPayload = await requestJson<AuthTokenResponse>('/auth/login', {
    method: 'POST',
    body: payload,
    retry: 0
  });
  setAuthToken(tokenPayload.access_token);
  return tokenPayload;
}

export async function logoutAuth() {
  try {
    await requestJson<AuthLogoutResponse>('/auth/logout', {
      method: 'POST',
      retry: 0
    });
  } finally {
    clearAuthToken();
  }
}

export async function fetchAuthMe() {
  return requestJson<AuthUserResponse>('/auth/me', { retry: 0 });
}

