const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8080';

export type Envelope<T> = {
  requestId: string;
  code: string;
  message: string;
  data: T;
};

export async function apiFetch<T>(path: string, options: RequestInit = {}, token?: string): Promise<Envelope<T>> {
  const headers = new Headers(options.headers || {});
  headers.set('Content-Type', headers.get('Content-Type') || 'application/json');
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers, cache: 'no-store' });
  const data = (await res.json()) as Envelope<T>;
  if (!res.ok) {
    throw new Error(data.message || 'request_failed');
  }
  return data;
}

export { API_BASE };
