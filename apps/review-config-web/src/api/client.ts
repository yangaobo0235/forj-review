export async function request<T>(
  path: string,
  body?: unknown,
  method?: string,
  signal?: AbortSignal,
): Promise<T> {
  const timeout = AbortSignal.timeout(15000);
  try {
    const response = await fetch("/api/review-config" + path, {
      method: method ?? (body === undefined ? "GET" : "POST"),
      headers: { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    });
    let value;
    try {
      value = await response.json();
    } catch (error) {
      if (signal?.aborted || timeout.aborted) throw error;
      throw new Error("配置服务暂不可用，请检查本机 26102 配置后端是否已启动", { cause: error });
    }
    if (response.status === 404 && path === "/captures") {
      throw new Error(
        "配置后端尚未提供页面采集接口，请重启本机 26102 配置后端以加载最新代码，再重新连接",
      );
    }
    if (!response.ok)
      throw new RequestError(
        value.message ??
          (response.status === 422
            ? "输入格式无效，请检查配置内容"
            : `请求失败（${response.status}）`),
        response.status,
        value.code,
      );
    return value as T;
  } catch (error) {
    if (timeout.aborted && !signal?.aborted)
      throw new Error("配置服务请求超时，请检查本机 26102 配置后端；会话状态读取将自动重试", {
        cause: error,
      });
    throw error;
  }
}

export const command = (revision: number, extra = {}) => ({
  client_request_id: crypto.randomUUID(),
  expected_revision: revision,
  ...extra,
});
export class RequestError extends Error {
  readonly status: number;
  readonly code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}
