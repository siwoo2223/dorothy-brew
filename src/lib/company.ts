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
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

let endpointUrl = '';
let token = '';

export function setCompanyConfig(url: string, secret: string) {
  endpointUrl = (url || '').trim();
  token = (secret || '').trim();
}

export function companyConfigured(): boolean {
  return !!endpointUrl && !!token;
}

/**
 * Loads the saved URL + token straight from storage and sets the module config. The background
 * check runs in a fresh JS context where the store never mounted, so it can't rely on
 * setCompanyConfig having been called; it calls this first. Returns whether a config is present.
 */
export async function loadCompanyConfig(): Promise<boolean> {
  try {
    const url = await AsyncStorage.getItem('dorothy.companyUrl');
    const secret = Platform.OS === 'web' ? await AsyncStorage.getItem('dorothy.companyToken') : await SecureStore.getItemAsync('dorothy.companyToken');
    setCompanyConfig(url ? JSON.parse(url) : '', secret ?? '');
  } catch {
    // leave whatever is already set
  }
  return companyConfigured();
}

/** Builds the endpoint URL with the given query params and fetches the response as text. */
async function get(params: Record<string, string>): Promise<string> {
  if (!endpointUrl || !token) {
    throw new Error('회사 현황이 아직 연결되지 않았어요. 설정 → "회사 현황 연결"에서 주소와 토큰을 넣어 주세요.');
  }
  const qs = Object.entries(params)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&');
  const sep = endpointUrl.includes('?') ? '&' : '?';
  let res: Response;
  try {
    res = await fetch(`${endpointUrl}${sep}${qs}`, {
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

/** Fetches the JSON summary (or the schema dump) as text, to hand straight to Claude. */
export async function fetchCompanyReport(mode: 'report' | 'schema' = 'report'): Promise<string> {
  return get({ mode });
}

/** "Needs attention today": this week's departing/arriving voyages, long-stuck shipments, unpaid hint. */
export async function fetchCompanyAttention(): Promise<string> {
  return get({ mode: 'attention' });
}

/**
 * Looks up one entity:
 *  - voyage:   query = voyage code (AIR/FROZEN/DRY + number, e.g. 'FROZEN17')
 *  - customer: query = name or code to search
 *  - tracking: query = tracking number (조회번호)
 */
export async function fetchCompanyLookup(type: 'voyage' | 'customer' | 'tracking', query: string): Promise<string> {
  const q = (query || '').trim();
  if (!q) throw new Error('무엇을 조회할지 값이 비어 있어요.');
  if (type === 'voyage') return get({ mode: 'voyage', code: q.toUpperCase() });
  if (type === 'customer') return get({ mode: 'customer', q });
  return get({ mode: 'track', no: q });
}
