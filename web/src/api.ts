// Thin client over the existing FastAPI endpoints. All permissions and rules stay on the server.

export type Role = "student" | "teacher";
export interface User { id: string; username: string; display_name: string; role: Role; must_change_password?: boolean; }
export interface Identity { mode: "local" | "demo"; token?: string; ownerId?: string; user: User; }

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) { super(message); }
}

const KEY = "clinic.identity";
let identity: Identity | null = (() => {
  try { return JSON.parse(sessionStorage.getItem(KEY) || "null"); } catch { return null; }
})();
const listeners = new Set<() => void>();

export function getIdentity() { return identity; }
export function setIdentity(next: Identity | null) {
  identity = next;
  if (next) sessionStorage.setItem(KEY, JSON.stringify(next)); else sessionStorage.removeItem(KEY);
  pending.clear();
  listeners.forEach((fn) => fn());
}
export function onIdentityChange(fn: () => void) { listeners.add(fn); return () => listeners.delete(fn); }

function headers(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (identity?.token) h.Authorization = `Bearer ${identity.token}`;
  else if (identity?.ownerId) h["X-Owner-Id"] = identity.ownerId;
  return h;
}

function describe(status: number, body: any): string {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (detail?.issues) return detail.issues.map((i: any) => i.message).join("；");
  if (Array.isArray(detail)) return "提交内容格式不正确，请检查后重试";
  if (status === 401) return "登录已失效，请重新登录";
  if (status === 403) return "当前账号没有权限执行此操作";
  if (status === 404) return "未找到对应记录";
  if (status >= 500) return "服务暂时不可用，请稍后重试";
  return `请求失败（${status}）`;
}

export async function request<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { method, headers: headers(), body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "无法连接服务，请检查后端是否已启动");
  }
  const text = await response.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!response.ok) {
    if (response.status === 401 && identity?.mode === "local" && !path.startsWith("/api/auth/")) setIdentity(null);
    throw new ApiError(response.status, describe(response.status, data), data?.detail);
  }
  return data as T;
}

export const get = <T = any>(path: string) => request<T>("GET", path);

/** GET that maps 404 to null (e.g. feedback not generated yet). */
export async function getOptional<T = any>(path: string): Promise<T | null> {
  try { return await get<T>(path); } catch (e) { if (e instanceof ApiError && e.status === 404) return null; throw e; }
}

// Idempotent writes: a retry of the same payload in the same scope reuses its request_id, so a
// lost response never executes an action twice. A changed payload gets a fresh id.
const pending = new Map<string, { payload: string; requestId: string }>();

export async function mutate<T = any>(scope: string, method: string, path: string, payload: Record<string, unknown>): Promise<T> {
  const serialized = JSON.stringify(payload);
  let entry = pending.get(scope);
  if (!entry || entry.payload !== serialized) {
    entry = { payload: serialized, requestId: uuid() };
    pending.set(scope, entry);
  }
  const result = await request<T>(method, path, { ...payload, request_id: entry.requestId });
  pending.delete(scope);
  return result;
}

export const enc = encodeURIComponent;

export function uuid(): string {
  if (typeof crypto.randomUUID === "function" && window.isSecureContext) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
