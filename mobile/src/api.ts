import { API_URL } from './config';

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

export async function api<T>(path: string, opts: { method?: string; body?: unknown; token?: string | null; timeoutMs?: number } = {}): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 30000);
  try {
    const res = await fetch(`${API_URL}${path}`, {
      method: opts.method ?? (opts.body ? 'POST' : 'GET'),
      headers: {
        'Content-Type': 'application/json',
        ...(opts.token ? { Authorization: `Bearer ${opts.token}` } : {}),
      },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      signal: ctrl.signal,
    });
    const text = await res.text();
    const data = text ? JSON.parse(text) : {};
    if (!res.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : `HTTP ${res.status}`, res.status);
    return data as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError("Can't reach ShopSmart right now - you're offline.", 0);
  } finally {
    clearTimeout(timer);
  }
}

export type Persona = {
  shopper_id: string;
  name: string;
  last_name: string;
  member_number: string;
  city: string;
  tier: string;
  smart_cart_items: number;
  home_store: string | null;
  blurb: string;
};

export type LoginResponse = {
  token: string;
  shopper_id: string;
  shopper: { first_name: string; last_name: string; email: string | null; tier: string; tier_name: string; home_store: { store_id: string; name: string; city: string } | null };
  channels: string[];
  sync_gateway: { session_id: string; cookie_name: string; expires: string; db: string; url: string | null } | null;
  export_date: string;
};
