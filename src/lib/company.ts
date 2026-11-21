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

/**
 * 받아쓰기 정확도용 용어 힌트: 사장님이 자주 쓰는 물류·업무 고유명사. Gemini에 함께 넘겨
 * 비슷하게 들리는 말(예: '출항'을 '차량'으로)을 맞는 단어로 인식하게 한다.
 */
export const VOICE_VOCAB =
  '출항, 입항, 입고, 통관, 통관사, 냉동, 냉장, 드라이, 항공, 항차, 선적, 부킹, 반입마감, 서류마감, CBM, 중량, 박스, 미수금, 견적, 정산, 송장, 송장번호, 조회번호, 노스포트, 마닐라, 세부, 클락, 앙헬레스, 보라카이, 다바오, 보홀, 컨테이너, 콘사이니, 리퍼, 페소, 환율, 테더, 기나글로벌, 기나, 손팀장, 양대리, 마진석부장, 한국지사, 윤사장님, 면장, 수출신고필증, 기적신고, FROZEN, DRY, KF 로지스틱스';

/** 회사 서버 Gemini로 음성(오디오 파일)을 정확히 받아쓴다. 실패하면 '' 반환(호출측이 폰 인식으로 대체). vocab은 자주 쓰는 용어 힌트. */
export async function transcribeAudio(audioUri: string, vocab?: string): Promise<string> {
  if (!endpointUrl || !token || !audioUri) return '';
  const url = endpointUrl.includes('dorothy_report.php')
    ? endpointUrl.replace('dorothy_report.php', 'dorothy_transcribe.php')
    : endpointUrl.replace(/[^/]*$/, 'dorothy_transcribe.php');
  try {
    const FileSystem = await import('expo-file-system/legacy');
    const base64 = await FileSystem.readAsStringAsync(audioUri, { encoding: FileSystem.EncodingType.Base64 });
    if (!base64) return '';
    // 너무 오래 매달리지 않도록 타임아웃(초과하면 ''로 → 호출측이 폰 인식 결과로 보냄).
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 7000);
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'X-Dorothy-Token': token, 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ audio_base64: base64, mime: 'audio/wav', ...(vocab ? { vocab: vocab.slice(0, 2000) } : {}) }),
      signal: ctrl.signal,
    }).finally(() => clearTimeout(timer));
    if (!res.ok) return '';
    const data = JSON.parse(await res.text());
    const text = typeof data?.text === 'string' ? data.text.trim() : '';
    return text;
  } catch {
    return '';
  }
}

/**
 * AI 이미지 생성: 프롬프트를 회사 서버(Gemini 이미지 모델)로 보내 그림을 받아, 그 base64 PNG를
 * 돌려준다. 실패하면 null. 회사 서버가 연결돼 있어야 한다.
 */
export async function generateImage(prompt: string): Promise<{ base64: string; mime: string } | null> {
  if (!endpointUrl || !token || !prompt.trim()) return null;
  const url = endpointUrl.includes('dorothy_report.php')
    ? endpointUrl.replace('dorothy_report.php', 'dorothy_image.php')
    : endpointUrl.replace(/[^/]*(\?.*)?$/, 'dorothy_image.php');
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 60000); // 이미지 생성은 느릴 수 있음
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'X-Dorothy-Token': token, 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ prompt: prompt.slice(0, 2000) }),
      signal: ctrl.signal,
    }).finally(() => clearTimeout(timer));
    if (!res.ok) return null;
    const data = JSON.parse(await res.text());
    if (data?.ok && typeof data.image_base64 === 'string' && data.image_base64) {
      return { base64: data.image_base64, mime: typeof data.mime === 'string' ? data.mime : 'image/png' };
    }
    return null;
  } catch {
    return null;
  }
}

/** Fetches the JSON summary (or the schema dump) as text, to hand straight to Claude. */
export async function fetchCompanyReport(mode: 'report' | 'schema' = 'report'): Promise<string> {
  return get({ mode });
}

/** "Needs attention today": this week's departing/arriving voyages, long-stuck shipments, unpaid hint. */
export async function fetchCompanyAttention(): Promise<string> {
  return get({ mode: 'attention' });
}

/** KF 자체 출항 스케줄(항차 목록): 항차코드·선사(mode)·차수·루트·출항일·도착일 등. 서버가 실시간으로 갱신. */
export async function fetchCompanyVoyages(): Promise<string> {
  return get({ mode: 'voyages' });
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

/**
 * The full shipment list (조회번호·항차·고객·품목·부피·중량·상태·날짜), with optional filters and
 * pagination (50 per page). All fields optional; empty returns the most recent page.
 */
export async function fetchCompanyShipments(opts: {
  status?: string;
  code?: string;
  q?: string;
  from?: string;
  to?: string;
  page?: number;
}): Promise<string> {
  const p: Record<string, string> = { mode: 'shipments' };
  if (opts.status) p.status = opts.status;
  if (opts.code) p.code = opts.code.toUpperCase();
  if (opts.q) p.q = opts.q;
  if (opts.from) p.from = opts.from;
  if (opts.to) p.to = opts.to;
  if (opts.page && opts.page > 1) p.page = String(opts.page);
  return get(p);
}

/** The computed receipt amount for one shipment (real receipt logic): total, basis, outstanding. */
export async function fetchCompanyQuote(trackingNo: string): Promise<string> {
  const no = (trackingNo || '').trim();
  if (!no) throw new Error('조회번호가 비어 있어요.');
  return get({ mode: 'quote', no });
}

/**
 * The owner's own work-pattern analysis lives in a separate endpoint (dorothy_pattern.php)
 * that sits next to the report endpoint and takes the same token. Derive its URL from the
 * configured report URL so the user doesn't have to enter a second address.
 */
function patternUrl(): string {
  if (!endpointUrl) return '';
  if (endpointUrl.includes('dorothy_report.php')) return endpointUrl.replace('dorothy_report.php', 'dorothy_pattern.php');
  // Fall back: swap the last path segment (and any query) for the pattern file.
  return endpointUrl.replace(/[^/]*(\?.*)?$/, 'dorothy_pattern.php');
}

/** Fetches the owner's work-pattern JSON (hours/weekdays/work-type distribution + summary) as text. */
export async function fetchCompanyPattern(days = 90): Promise<string> {
  const base = patternUrl();
  if (!base || !token) {
    throw new Error('회사 현황이 아직 연결되지 않았어요. 설정 → "회사 현황 연결"에서 주소와 토큰을 넣어 주세요.');
  }
  const url = `${base}${base.includes('?') ? '&' : '?'}days=${encodeURIComponent(String(days))}`;
  let res: Response;
  try {
    res = await fetch(url, { method: 'GET', headers: { 'X-Dorothy-Token': token, Accept: 'application/json' } });
  } catch {
    throw new Error('회사 서버에 연결할 수 없어요. 주소가 맞는지, 인터넷이 되는지 확인해 주세요.');
  }
  const text = await res.text();
  if (res.status === 401 || res.status === 403) throw new Error('회사 서버가 접근을 거부했어요(토큰 불일치). 설정의 토큰을 확인해 주세요.');
  if (!res.ok) throw new Error(`회사 서버 오류 (${res.status}): ${text.slice(0, 300)}`);
  return text;
}

/** The funds/P&L endpoint (dorothy_finance.php) sits next to the report endpoint, same token. */
function financeUrl(): string {
  if (!endpointUrl) return '';
  if (endpointUrl.includes('dorothy_report.php')) return endpointUrl.replace('dorothy_report.php', 'dorothy_finance.php');
  return endpointUrl.replace(/[^/]*(\?.*)?$/, 'dorothy_finance.php');
}

/** Funds on hand + this month's revenue/deposits/expense/profit, per currency (matches the admin accounting page). */
export async function fetchCompanyFinance(accountId?: number): Promise<string> {
  const base = financeUrl();
  if (!base || !token) {
    throw new Error('회사 현황이 아직 연결되지 않았어요. 설정 → "회사 현황 연결"에서 주소와 토큰을 넣어 주세요.');
  }
  const url = accountId ? `${base}${base.includes('?') ? '&' : '?'}account=${encodeURIComponent(String(accountId))}` : base;
  let res: Response;
  try {
    res = await fetch(url, { method: 'GET', headers: { 'X-Dorothy-Token': token, Accept: 'application/json' } });
  } catch {
    throw new Error('회사 서버에 연결할 수 없어요. 주소가 맞는지, 인터넷이 되는지 확인해 주세요.');
  }
  const text = await res.text();
  if (res.status === 401 || res.status === 403) throw new Error('회사 서버가 접근을 거부했어요(토큰 불일치). 설정의 토큰을 확인해 주세요.');
  if (res.status === 404) throw new Error('자금 API(dorothy_finance.php)가 서버에 아직 없어요. 회사 서버 배포를 확인해 주세요.');
  if (!res.ok) throw new Error(`회사 서버 오류 (${res.status}): ${text.slice(0, 300)}`);
  return text;
}

/**
 * The generic read-only data endpoint (dorothy_data.php) sits next to the report endpoint and takes
 * the same token — so the owner doesn't enter a second address. It lets the assistant read ANY table
 * on the site (SELECT only; sensitive tables/columns are blocked/redacted server-side).
 */
function dataUrl(): string {
  if (!endpointUrl) return '';
  if (endpointUrl.includes('dorothy_report.php')) return endpointUrl.replace('dorothy_report.php', 'dorothy_data.php');
  return endpointUrl.replace(/[^/]*(\?.*)?$/, 'dorothy_data.php');
}

async function getData(params: Record<string, string>): Promise<string> {
  const base = dataUrl();
  if (!base || !token) {
    throw new Error('회사 현황이 아직 연결되지 않았어요. 설정 → "회사 현황 연결"에서 주소와 토큰을 넣어 주세요.');
  }
  const qs = Object.entries(params)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&');
  const url = `${base}${base.includes('?') ? '&' : '?'}${qs}`;
  let res: Response;
  try {
    res = await fetch(url, { method: 'GET', headers: { 'X-Dorothy-Token': token, Accept: 'application/json' } });
  } catch {
    throw new Error('회사 서버에 연결할 수 없어요. 주소가 맞는지, 인터넷이 되는지 확인해 주세요.');
  }
  const text = await res.text();
  if (res.status === 401 || res.status === 403) throw new Error('회사 서버가 접근을 거부했어요(토큰 불일치). 설정의 토큰을 확인해 주세요.');
  if (res.status === 404) throw new Error('범용 읽기 API(dorothy_data.php)가 서버에 아직 없어요. 회사 서버 배포를 확인해 주세요.');
  if (!res.ok) throw new Error(`회사 서버 오류 (${res.status}): ${text.slice(0, 300)}`);
  return text;
}

/** Lists the tables Dorothy may read on the company site (+ row counts). */
export async function fetchCompanyTables(): Promise<string> {
  return getData({ mode: 'tables' });
}

/** Lists one table's columns (name/type). */
export async function fetchCompanyColumns(table: string): Promise<string> {
  const t = (table || '').trim();
  if (!t) throw new Error('테이블 이름이 비어 있어요.');
  return getData({ mode: 'columns', table: t });
}

/** Reads rows from one table, with optional text search / exact filter / order / paging. */
export async function fetchCompanyRows(opts: {
  table: string;
  limit?: number;
  offset?: number;
  q?: string;
  whereCol?: string;
  whereVal?: string;
  order?: string;
  dir?: 'asc' | 'desc';
}): Promise<string> {
  const t = (opts.table || '').trim();
  if (!t) throw new Error('테이블 이름이 비어 있어요.');
  const p: Record<string, string> = { mode: 'rows', table: t };
  if (opts.limit) p.limit = String(opts.limit);
  if (opts.offset) p.offset = String(opts.offset);
  if (opts.q) p.q = opts.q;
  if (opts.whereCol) { p.where_col = opts.whereCol; p.where_val = opts.whereVal ?? ''; }
  if (opts.order) { p.order = opts.order; if (opts.dir) p.dir = opts.dir; }
  return getData(p);
}

/**
 * Intake photos for one shipment (조회번호), as ready-to-display absolute image URLs. The endpoint
 * (dorothy_photos.php) is a sibling of the report endpoint and takes the same token.
 */
export async function fetchCompanyPhotos(trackingNo: string): Promise<string> {
  const no = (trackingNo || '').trim();
  if (!no) throw new Error('조회번호가 비어 있어요.');
  if (!endpointUrl || !token) {
    throw new Error('회사 현황이 아직 연결되지 않았어요. 설정 → "회사 현황 연결"에서 주소와 토큰을 넣어 주세요.');
  }
  const base = endpointUrl.includes('dorothy_report.php')
    ? endpointUrl.replace('dorothy_report.php', 'dorothy_photos.php')
    : endpointUrl.replace(/[^/]*(\?.*)?$/, 'dorothy_photos.php');
  const url = `${base}${base.includes('?') ? '&' : '?'}no=${encodeURIComponent(no)}`;
  let res: Response;
  try {
    res = await fetch(url, { method: 'GET', headers: { 'X-Dorothy-Token': token, Accept: 'application/json' } });
  } catch {
    throw new Error('회사 서버에 연결할 수 없어요. 주소가 맞는지, 인터넷이 되는지 확인해 주세요.');
  }
  const text = await res.text();
  if (res.status === 401 || res.status === 403) throw new Error('회사 서버가 접근을 거부했어요(토큰 불일치). 설정의 토큰을 확인해 주세요.');
  if (res.status === 404) throw new Error('사진 조회 API(dorothy_photos.php)가 서버에 아직 없어요. 회사 서버 배포를 확인해 주세요.');
  if (!res.ok) throw new Error(`회사 서버 오류 (${res.status}): ${text.slice(0, 300)}`);
  return text;
}
