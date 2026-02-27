export const DEFAULT_DEV_API_BASE = 'http://127.0.0.1:8766/api/v1';
const SAME_ORIGIN_FALLBACK_API_BASE = '/api/v1';

type ApiBaseState = {
  base: string;
  source: 'env' | 'dev-default' | 'same-origin-fallback';
  warning: string;
};

function trimTrailingSlash(value: string) {
  return String(value || '').replace(/\/+$/, '');
}

function isLocalHostName(hostname: string) {
  const safe = String(hostname || '').trim().toLowerCase();
  return safe === 'localhost' || safe === '127.0.0.1' || safe === '::1' || safe === '[::1]';
}

function isLikelyLocalApiUrl(url: string) {
  const safe = String(url || '').trim().toLowerCase();
  return safe.startsWith('http://127.0.0.1:') || safe.startsWith('http://localhost:');
}

function resolveApiBaseState(value: string | undefined | null): ApiBaseState {
  const fromEnv = trimTrailingSlash(String(value || '').trim());
  const browserHost = typeof window === 'undefined' ? '' : String(window.location.hostname || '').trim();
  const localRuntime = isLocalHostName(browserHost);
  if (fromEnv) {
    const warning =
      !localRuntime && isLikelyLocalApiUrl(fromEnv)
        ? '检测到线上环境仍在使用本地 API 地址，请在部署平台设置 VITE_SUBTITLE_API_BASE 指向后端域名的 /api/v1。'
        : '';
    return {
      base: fromEnv,
      source: 'env',
      warning
    };
  }
  if (localRuntime || typeof window === 'undefined') {
    return {
      base: DEFAULT_DEV_API_BASE,
      source: 'dev-default',
      warning: ''
    };
  }
  return {
    base: SAME_ORIGIN_FALLBACK_API_BASE,
    source: 'same-origin-fallback',
    warning: '缺少 VITE_SUBTITLE_API_BASE，已回退到同域 /api/v1。若未配置反向代理，请改为后端域名的 /api/v1。'
  };
}

const API_BASE_STATE = resolveApiBaseState(import.meta.env.VITE_SUBTITLE_API_BASE);

if (API_BASE_STATE.warning && typeof window !== 'undefined') {
  console.warn(`[DEBUG] ${API_BASE_STATE.warning}`);
}

export function resolveApiBase(value: string | undefined | null) {
  return resolveApiBaseState(value).base;
}

export function getApiBaseDiagnostics() {
  return API_BASE_STATE;
}

export function getApiBase() {
  return API_BASE_STATE.base;
}
