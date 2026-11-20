/**
 * Dorothy's "my own server" brain: an open model the owner runs on their OWN computer or server
 * (an AI PC, a home box, a company server) and exposes over the network. Nothing leaves the owner's
 * machines, and there's no per-message cost — the long-term "나만의 AI" path.
 *
 * It talks the OpenAI-compatible Chat Completions API, which every common self-host server speaks:
 * Ollama (`ollama serve`, :11434), llama.cpp server, LM Studio, vLLM, text-generation-webui, …
 * So the owner just points Dorothy at the address; no custom protocol to maintain.
 *
 * Kept deliberately simple and separate from the Claude path so it can never destabilise daily use:
 * a plain non-streaming POST (React Native can't reliably stream a fetch body), tools off for now.
 */

export type ServerBrainConfig = {
  /** Base address of the server, e.g. "http://192.168.0.10:11434" or "https://ai.mydomain.com". */
  url: string;
  /** Model name the server should load, e.g. "qwen2.5:7b" (Ollama) or "llama-3.1-8b". */
  model: string;
  /** Optional API key/token, if the server requires one (many home servers don't). */
  key: string;
};

export type ServerMessage = { role: 'system' | 'user' | 'assistant'; content: string };

export function serverBrainConfigured(c?: ServerBrainConfig | null): boolean {
  return !!c && !!c.url?.trim();
}

/** Normalises whatever the owner pasted into the real chat-completions endpoint. */
function chatEndpoint(base: string): string {
  const u = base.trim().replace(/\/+$/, '');
  if (/\/chat\/completions$/.test(u)) return u;
  if (/\/v1$/.test(u)) return `${u}/chat/completions`;
  return `${u}/v1/chat/completions`;
}

/** The server's root (without the /v1… suffix), used for /v1/models. */
function apiRoot(base: string): string {
  return base.trim().replace(/\/+$/, '').replace(/\/chat\/completions$/, '').replace(/\/v1$/, '');
}

function authHeaders(key: string): Record<string, string> {
  const k = key?.trim();
  return k ? { Authorization: `Bearer ${k}` } : {};
}

async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs: number): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (e) {
    if (e instanceof Error && e.name === 'AbortError') {
      throw new Error('서버 두뇌가 제시간에 답하지 않았어요. 주소·네트워크를 확인해 주세요.');
    }
    // RN gives a bare "Network request failed" for a wrong address / server off / blocked cleartext.
    throw new Error('서버 두뇌에 연결하지 못했어요. 주소가 맞는지, 서버가 켜져 있는지, 같은 네트워크인지 확인해 주세요.');
  } finally {
    clearTimeout(timer);
  }
}

/** One completion on the owner's server. onToken is accepted for API parity but not streamed. */
export async function serverComplete(
  config: ServerBrainConfig,
  messages: ServerMessage[],
  maxTokens = 800,
  _onToken?: (token: string) => void,
): Promise<string> {
  if (!serverBrainConfigured(config)) {
    throw new Error('서버 두뇌 주소가 아직 없어요. 설정 → 도로시의 두뇌 → 내 서버 두뇌에서 주소를 넣어 주세요.');
  }
  const body: Record<string, unknown> = {
    messages,
    stream: false,
    temperature: 0.6,
    max_tokens: maxTokens,
  };
  // Many servers (Ollama, vLLM) require a model name; llama.cpp ignores it. Send it only when set.
  if (config.model?.trim()) body.model = config.model.trim();

  const res = await fetchWithTimeout(
    chatEndpoint(config.url),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders(config.key) },
      body: JSON.stringify(body),
    },
    90000, // self-hosted models can be slow on first token
  );
  if (!res.ok) {
    const detail = (await res.text().catch(() => '')).slice(0, 200);
    if (res.status === 404) throw new Error('서버에 그 주소/모델이 없어요. 모델 이름과 주소를 확인해 주세요.');
    if (res.status === 401 || res.status === 403) throw new Error('서버 두뇌 인증에 실패했어요. API 키를 확인해 주세요.');
    throw new Error(`서버 두뇌 오류 (HTTP ${res.status})${detail ? ` · ${detail}` : ''}`);
  }
  const data = (await res.json().catch(() => null)) as
    | { choices?: { message?: { content?: string } }[] }
    | null;
  const content = data?.choices?.[0]?.message?.content;
  if (typeof content !== 'string' || !content.trim()) {
    throw new Error('서버 두뇌가 빈 답을 보냈어요. 모델이 제대로 떠 있는지 확인해 주세요.');
  }
  return content.trim();
}

/** Connection test: returns the model names the server reports, so the owner can confirm it works. */
export async function pingServerBrain(config: ServerBrainConfig): Promise<string[]> {
  if (!serverBrainConfigured(config)) throw new Error('먼저 서버 주소를 넣어 주세요.');
  const res = await fetchWithTimeout(
    `${apiRoot(config.url)}/v1/models`,
    { headers: { ...authHeaders(config.key) } },
    20000,
  );
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) throw new Error('인증 실패 — API 키를 확인해 주세요.');
    throw new Error(`연결은 됐지만 모델 목록을 못 읽었어요 (HTTP ${res.status}).`);
  }
  const data = (await res.json().catch(() => null)) as { data?: { id?: string }[] } | null;
  const ids = Array.isArray(data?.data)
    ? data!.data!.map((m) => m?.id).filter((x): x is string => typeof x === 'string' && !!x.trim())
    : [];
  return ids;
}
