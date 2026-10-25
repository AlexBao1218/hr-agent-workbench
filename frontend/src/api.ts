export class ApiRequestError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers:
        body instanceof FormData
          ? undefined
          : { "Content-Type": "application/json" },
      body:
        body === undefined
          ? undefined
          : body instanceof FormData
            ? body
            : JSON.stringify(body),
    });
  } catch {
    throw new Error("无法连接本地服务。请启动 127.0.0.1:8000 后重试。");
  }
  const text = await response.text();
  let data: any;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`服务返回无效响应 (${response.status})，请检查后端。`);
  }
  if (!response.ok)
    throw new ApiRequestError(
      data?.error?.message || `请求失败 (${response.status})`,
      response.status,
      data?.error?.code,
    );
  if (method !== "GET" && data?.runId)
    window.dispatchEvent(new CustomEvent("hr-run", { detail: data.runId }));
  return data as T;
}
