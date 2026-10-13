/**
 * Reads a read-only "company status" summary from the user's own server
 * (e.g. kflogistics-group.com/backend/api/dorothy_report.php). The endpoint is
 * protected by a secret token; the URL and token live only on this device
 * (the token in SecureStore, like the API key) and are set in Settings.
 *
 * Config is kept in module scope and refreshed by the store (same pattern as
 * the TTS voice), so the assistant's tool loop can reach it without threading
 * state through every call.
 */
let endpointUrl = '';
let token = '';

export function setCompanyConfig(url: string, secret: string) {
  endpointUrl = (url || '').trim();
  token = (secret || '').trim();
}

export function companyConfigured(): boolean {
  return !!endpointUrl && !!token;
}

/** Fetches the JSON summary (or the schema dump) as text, to hand straight to Claude. */
export async function fetchCompanyReport(mode: 'report' | 'schema' = 'report'): Promise<string> {
  if (!endpointUrl || !token) {
    throw new Error('회사 현황이 아직 연결되지 않았어요. 설정 → "회사 현황 연결"에서 주소와 토큰을 넣어 주세요.');
  }
  const sep = endpointUrl.includes('?') ? '&' : '?';
  let res: Response;
  try {
    res = await fetch(`${endpointUrl}${sep}mode=${mode}`, {
      method: 'GET',
      headers: { 'X-Dorothy-Token': token, Accept: 'application/json' },
    });
  } catch {
    throw new Error('회사 서버에 연결할 수 없어요. 주소가 맞는지, 인터넷이 되는지 확인해 주세요.');
  }
  const text = await res.text();
  if (res.status === 403) throw new Error('회사 서버가 접근을 거부했어요(토큰 불일치). 설정의 토큰을 확인해 주세요.');
  if (!res.ok) throw new Error(`회사 서버 오류 (${res.status}): ${text.slice(0, 300)}`);
  return text;
}
