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
