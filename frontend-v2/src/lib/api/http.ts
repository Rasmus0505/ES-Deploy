import { getApiBase, getApiBaseDiagnostics } from './base';
import { clearAuthToken, getAuthToken } from './auth-token';

const API_BASE_INFO = getApiBaseDiagnostics();
const API_BASE = getApiBase();

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

type RequestOptions = Omit<RequestInit, 'body'> & {
  body?: BodyInit | object | null;
  retry?: number;
  retryDelayMs?: number;
};

export class ApiRequestError extends Error {
  status: number;

  payload: unknown;

  constructor(message: string, status = 0, payload: unknown = null) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.payload = payload;
  }
}

function extractDetailMessage(detail: unknown): string {
  if (!detail) return '';
  if (typeof detail === 'string') return detail.trim();
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => extractDetailMessage(item))
      .filter(Boolean);
    return parts.join('; ').trim();
  }
  if (typeof detail === 'object') {
    const payload = detail as Record<string, unknown>;
    const message = typeof payload.message === 'string' ? payload.message.trim() : '';
    const error = typeof payload.error === 'string' ? payload.error.trim() : '';
    const code = typeof payload.code === 'string' ? payload.code.trim() : '';
    if (message && code) return `${message} (${code})`;
    if (message) return message;
    if (error) return error;
    if (code) return code;
  }
  return '';
}

function extractPayloadMessage(payload: unknown): string {
  if (!payload) return '';
  if (typeof payload === 'string') return payload.trim();
  if (typeof payload !== 'object') return '';

  const data = payload as Record<string, unknown>;
  const detailMessage = extractDetailMessage(data.detail);
  if (detailMessage) return detailMessage;

  const message = typeof data.message === 'string' ? data.message.trim() : '';
  if (message) return message;

  const error = typeof data.error === 'string' ? data.error.trim() : '';
  if (error) return error;

  return '';
}

function isHtmlPayload(payload: unknown) {
  if (typeof payload !== 'string') return false;
  const snippet = payload.trim().toLowerCase();
  if (!snippet) return false;
  return snippet.startsWith('<!doctype html') || snippet.startsWith('<html') || snippet.includes('<body');
}

function buildApiConfigHint() {
  const parts: string[] = [];
  if (API_BASE_INFO.warning) {
    parts.push(API_BASE_INFO.warning);
  }
  parts.push(`当前 API 地址：${API_BASE}`);
  return parts.join(' ');
}

function buildPathHint(path: string, status: number, payload: unknown) {
  const safePath = String(path || '').trim();
  if (isHtmlPayload(payload)) {
    return `接口返回了网页内容，说明请求没有到达后端 API。请检查部署路由与 VITE_SUBTITLE_API_BASE。${buildApiConfigHint()}`;
  }
  if (status === 404 && safePath.startsWith('/auth/')) {
    return `认证接口不存在（${safePath}）。请确认后端已部署且路径包含 /api/v1/auth/*。${buildApiConfigHint()}`;
  }
  if (status === 404) {
    return `接口不存在（${safePath}）。请检查后端域名和 API 前缀配置。${buildApiConfigHint()}`;
  }
  if (status === 502 || status === 503 || status === 504) {
    return `后端服务暂时不可用，请稍后重试。若持续失败，请检查后端服务状态与网关配置。${buildApiConfigHint()}`;
  }
  return '';
}

function buildNetworkHint(path: string) {
  const safePath = String(path || '').trim();
  return `无法连接到后端接口（${safePath}）。请检查后端域名、CORS_ALLOW_ORIGINS 和 VITE_SUBTITLE_API_BASE。${buildApiConfigHint()}`;
}

async function parsePayload(response: Response) {
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    try {
      return await response.json();
    } catch {
      return null;
    }
  }
  try {
    return await response.text();
  } catch {
    return null;
  }
}

function buildRequestInit(options: RequestOptions): RequestInit {
  const headers = new Headers(options.headers || {});
  const token = getAuthToken();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const init: RequestInit = {
    method: options.method || 'GET',
    headers,
    signal: options.signal
  };

  if (options.body === undefined || options.body === null) {
    return init;
  }

  if (options.body instanceof FormData || typeof options.body === 'string' || options.body instanceof Blob) {
    init.body = options.body;
    return init;
  }

  headers.set('Content-Type', 'application/json');
  init.body = JSON.stringify(options.body);
  return init;
}

async function request(path: string, options: RequestOptions = {}) {
  const retry = Number.isFinite(options.retry) ? Number(options.retry) : 1;
  const retryDelayMs = Number.isFinite(options.retryDelayMs) ? Number(options.retryDelayMs) : 450;
  const url = `${API_BASE}${path}`;
  const init = buildRequestInit(options);
  let lastError: unknown = null;

  for (let attempt = 0; attempt <= retry; attempt += 1) {
    try {
      const response = await fetch(url, init);
      if (!response.ok) {
        const payload = await parsePayload(response);
        const baseMessage = extractPayloadMessage(payload) || `HTTP ${response.status}`;
        const hinted = buildPathHint(path, response.status, payload);
        const message = hinted || baseMessage;
        if (response.status === 401) {
          clearAuthToken();
        }
        const error = new ApiRequestError(message, response.status, payload);
        if (response.status >= 500 && attempt < retry) {
          await sleep(retryDelayMs * (attempt + 1));
          continue;
        }
        throw error;
      }
      return response;
    } catch (error) {
      lastError = error;
      if (attempt >= retry) {
        if (error instanceof ApiRequestError) throw error;
        throw new ApiRequestError(buildNetworkHint(path), 0, {
          cause: error instanceof Error ? error.message : String(error || '')
        });
      }
      if (error instanceof ApiRequestError && error.status >= 400 && error.status < 500) throw error;
      await sleep(retryDelayMs * (attempt + 1));
    }
  }

  throw lastError instanceof Error ? lastError : new Error('Unknown request error');
}

export async function requestJson<T>(path: string, options: RequestOptions = {}) {
  const response = await request(path, options);
  const contentType = String(response.headers.get('content-type') || '').toLowerCase();
  const payload = await parsePayload(response);
  if (!contentType.includes('application/json')) {
    throw new ApiRequestError(
      buildPathHint(path, response.status || 200, payload) || `接口返回非 JSON 内容，无法解析：${path}`,
      response.status || 0,
      payload
    );
  }
  if (typeof payload === 'string' && isHtmlPayload(payload)) {
    throw new ApiRequestError(
      buildPathHint(path, response.status || 200, payload) || `接口返回 HTML，无法解析 JSON：${path}`,
      response.status || 0,
      payload
    );
  }
  return payload as T;
}

export async function requestBlob(path: string, options: RequestOptions = {}) {
  const response = await request(path, options);
  const blob = await response.blob();
  const contentDisposition = response.headers.get('content-disposition') || '';
  const filenameMatch = contentDisposition.match(/filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?/i);
  const filename = filenameMatch ? decodeURIComponent(filenameMatch[1]) : '';
  return { blob, filename };
}

window.addEventListener('offline', () => {
  console.warn(`[DEBUG] 浏览器已离线，API 请求将失败。${buildNetworkHint('/api/v1/*')}`);
});

export { getApiBase } from './base';
