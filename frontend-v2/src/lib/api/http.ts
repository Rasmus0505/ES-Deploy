import { getApiBase } from './base';

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
        const message = extractPayloadMessage(payload) || `HTTP ${response.status}`;
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
      if (attempt >= retry) throw error;
      if (error instanceof ApiRequestError && error.status >= 400 && error.status < 500) throw error;
      await sleep(retryDelayMs * (attempt + 1));
    }
  }

  throw lastError instanceof Error ? lastError : new Error('Unknown request error');
}

export async function requestJson<T>(path: string, options: RequestOptions = {}) {
  const response = await request(path, options);
  return (await parsePayload(response)) as T;
}

export async function requestBlob(path: string, options: RequestOptions = {}) {
  const response = await request(path, options);
  const blob = await response.blob();
  const contentDisposition = response.headers.get('content-disposition') || '';
  const filenameMatch = contentDisposition.match(/filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?/i);
  const filename = filenameMatch ? decodeURIComponent(filenameMatch[1]) : '';
  return { blob, filename };
}

export { getApiBase } from './base';
