import { request, RequestError } from "./client";

/** Unknown network outcomes retry the exact request; known rejections may be corrected. */
export async function retryableCommand<T>(
  key: string,
  path: string,
  body: Record<string, unknown>,
  identity: string,
): Promise<T> {
  const previous = localStorage.getItem(key);
  const saved = previous
    ? (JSON.parse(previous) as { path: string; identity: string; body: Record<string, unknown> })
    : null;
  const attempt =
    saved?.path === path && saved.identity === identity ? saved : { path, identity, body };
  localStorage.setItem(key, JSON.stringify(attempt));
  try {
    const value = await request<T>(path, attempt.body);
    localStorage.removeItem(key);
    return value;
  } catch (error) {
    if (error instanceof RequestError && error.status < 500) localStorage.removeItem(key);
    throw error;
  }
}
