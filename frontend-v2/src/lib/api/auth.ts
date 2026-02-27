import type {
  AuthLoginRequest,
  AuthLogoutResponse,
  AuthRegisterRequest,
  AuthTokenResponse,
  AuthUserResponse
} from '../../types/backend';
import { clearAuthToken, setAuthToken } from './auth-token';
import { ApiRequestError, requestJson } from './http';

function normalizeAuthError(error: unknown, fallback: string) {
  if (!(error instanceof ApiRequestError)) {
    return error instanceof Error ? error : new Error(fallback);
  }
  const text = String(error.message || '').trim();
  const lower = text.toLowerCase();
  if (error.status === 401) {
    return new Error('用户名或密码错误，请检查后重试。');
  }
  if (error.status === 409) {
    return new Error('用户名已存在，请更换用户名后重试。');
  }
  if (error.status === 404 || lower.includes('认证接口不存在')) {
    return new Error('认证服务路径异常，请检查后端部署地址和 /api/v1 前缀配置。');
  }
  if (lower.includes('接口返回了网页内容') || lower.includes('html')) {
    return new Error('当前请求命中了前端页面而不是后端接口，请检查 VITE_SUBTITLE_API_BASE。');
  }
  if (error.status === 0 || lower.includes('无法连接到后端接口')) {
    return new Error('无法连接认证服务，请检查后端服务状态、域名和跨域配置。');
  }
  if (error.status >= 500) {
    return new Error('认证服务暂时不可用，请稍后重试。');
  }
  return new Error(text || fallback);
}

export async function registerAuth(payload: AuthRegisterRequest) {
  try {
    const tokenPayload = await requestJson<AuthTokenResponse>('/auth/register', {
      method: 'POST',
      body: payload,
      retry: 0
    });
    setAuthToken(tokenPayload.access_token);
    return tokenPayload;
  } catch (error) {
    throw normalizeAuthError(error, '注册失败，请稍后重试');
  }
}

export async function loginAuth(payload: AuthLoginRequest) {
  try {
    const tokenPayload = await requestJson<AuthTokenResponse>('/auth/login', {
      method: 'POST',
      body: payload,
      retry: 0
    });
    setAuthToken(tokenPayload.access_token);
    return tokenPayload;
  } catch (error) {
    throw normalizeAuthError(error, '登录失败，请稍后重试');
  }
}

export async function logoutAuth() {
  try {
    await requestJson<AuthLogoutResponse>('/auth/logout', {
      method: 'POST',
      retry: 0
    });
  } catch (error) {
    throw normalizeAuthError(error, '退出登录失败');
  } finally {
    clearAuthToken();
  }
}

export async function fetchAuthMe() {
  try {
    return await requestJson<AuthUserResponse>('/auth/me', { retry: 0 });
  } catch (error) {
    throw normalizeAuthError(error, '登录态校验失败');
  }
}
