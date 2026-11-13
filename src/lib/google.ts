import * as AuthSession from 'expo-auth-session';
import * as FileSystem from 'expo-file-system/legacy';
import * as SecureStore from 'expo-secure-store';
import * as WebBrowser from 'expo-web-browser';
import { Platform } from 'react-native';

WebBrowser.maybeCompleteAuthSession();

/**
 * OAuth client IDs come from Google Cloud Console (APIs & Services → Credentials) and are set in `.env`.
 * They are public identifiers, not secrets; installed apps use PKCE instead of a client secret.
 */
const CLIENT_IDS = {
  ios: process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID ?? '',
  android: process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID ?? '',
};

export const SCOPES = [
  'openid',
  'email',
  'profile',
  'https://www.googleapis.com/auth/calendar.events',
  'https://www.googleapis.com/auth/gmail.readonly',
  'https://www.googleapis.com/auth/gmail.compose',
  // Lets Dorothy upload call recordings to Drive. drive.file only grants access to files the app
  // itself creates (not the user's other Drive files), so it is the least-privilege choice.
  'https://www.googleapis.com/auth/drive.file',
  // Read-only access to Google Sheets the user points Dorothy at (by pasting the link in Settings),
  // so she can read tables like the owner's '2026 리스트' / '지출' sheets.
  'https://www.googleapis.com/auth/spreadsheets.readonly',
];

export const discovery: AuthSession.DiscoveryDocument = {
  authorizationEndpoint: 'https://accounts.google.com/o/oauth2/v2/auth',
  tokenEndpoint: 'https://oauth2.googleapis.com/token',
  revocationEndpoint: 'https://oauth2.googleapis.com/revoke',
  userInfoEndpoint: 'https://openidconnect.googleapis.com/v1/userinfo',
};

export function googleClientId(): string {
  // The AppAuth browser flow redirects to the reversed-client-ID custom scheme, which Google only
  // accepts for iOS-type OAuth clients — even on Android (an Android client returns 400
  // invalid_request here). So prefer the iOS client; the Android client is a fallback.
  if (Platform.OS === 'web') return '';
  return CLIENT_IDS.ios || CLIENT_IDS.android;
}

/** Google redirects native clients to the reversed client ID scheme, which app.config.ts registers. */
export function googleRedirectUri(): string {
  const id = googleClientId();
  const reversed = id.split('.').reverse().join('.');
  return `${reversed}:/oauthredirect`;
}

type StoredToken = { accessToken: string; refreshToken?: string; expiresIn?: number; issuedAt: number; email: string };
const ACCOUNTS_KEY = 'dorothy.googleAccounts';
const LEGACY_KEY = 'dorothy.googleToken';
// Separates an account's email from a Gmail/Calendar id so an id handed to the assistant routes
// back to the account it came from. Chosen never to appear inside an email or a Google id.
const SEP = '|~|';

let cache: StoredToken[] | undefined;

async function loadAccounts(): Promise<StoredToken[]> {
  if (cache !== undefined) return cache;
  if (Platform.OS === 'web') return (cache = []);
  const raw = await SecureStore.getItemAsync(ACCOUNTS_KEY);
  if (raw) return (cache = JSON.parse(raw));
  // Migrate the old single-account token, if any.
  const legacy = await SecureStore.getItemAsync(LEGACY_KEY);
  if (legacy) {
    const t = JSON.parse(legacy) as Partial<StoredToken>;
    cache = t.accessToken ? [{ ...(t as StoredToken), email: t.email || '연결됨' }] : [];
    await SecureStore.setItemAsync(ACCOUNTS_KEY, JSON.stringify(cache));
    await SecureStore.deleteItemAsync(LEGACY_KEY);
    return cache;
  }
  return (cache = []);
}

async function saveAccounts(list: StoredToken[]) {
  cache = list;
  if (Platform.OS === 'web') return;
  if (list.length) await SecureStore.setItemAsync(ACCOUNTS_KEY, JSON.stringify(list));
  else await SecureStore.deleteItemAsync(ACCOUNTS_KEY);
}

async function upsert(token: StoredToken) {
  const list = (await loadAccounts()).filter((a) => a.email !== token.email);
  await saveAccounts([...list, token]);
}

export async function connectedEmails(): Promise<string[]> {
  return (await loadAccounts()).map((a) => a.email);
}

/** Finishes sign-in after the auth prompt returns a code. Adds or refreshes the account; returns its email. */
export async function completeSignIn(request: AuthSession.AuthRequest, code: string): Promise<string> {
  const response = await AuthSession.exchangeCodeAsync(
    { clientId: googleClientId(), code, redirectUri: googleRedirectUri(), extraParams: { code_verifier: request.codeVerifier ?? '' } },
    discovery,
  );
  const info = await AuthSession.fetchUserInfoAsync({ accessToken: response.accessToken }, discovery);
  const email = info.email ?? '연결됨';
  await upsert({
    accessToken: response.accessToken,
    refreshToken: response.refreshToken,
    expiresIn: response.expiresIn,
    issuedAt: response.issuedAt,
    email,
  });
  return email;
}

/** Disconnects one account by email, or every account when called with no argument. */
export async function disconnect(email?: string): Promise<void> {
  const list = await loadAccounts();
  const removing = email ? list.filter((a) => a.email === email) : list;
  for (const t of removing) {
    await AuthSession.revokeAsync({ token: t.refreshToken ?? t.accessToken, clientId: googleClientId() }, discovery).catch(() => {});
  }
  await saveAccounts(email ? list.filter((a) => a.email !== email) : []);
}

async function accessTokenFor(account: StoredToken, force = false): Promise<string> {
  // Refresh ~5 min before expiry so a long batch never carries a token that dies mid-run; `force`
  // skips the cache entirely, used to recover from a 401 (a token Google rejects though not yet expired).
  if (!force && AuthSession.TokenResponse.isTokenFresh(account, 300)) return account.accessToken;
  if (!account.refreshToken) {
    await disconnect(account.email);
    throw new Error(`Google 로그인이 만료됐어요 (${account.email}). 설정에서 다시 연결해 주세요.`);
  }
  let refreshed: AuthSession.TokenResponse;
  try {
    refreshed = await AuthSession.refreshAsync({ clientId: googleClientId(), refreshToken: account.refreshToken }, discovery);
  } catch {
    // A stale/invalid refresh token (e.g. carried over from an older build or a different client,
    // or a revoked grant) fails here as "invalid_grant". Clear it so the next connect starts clean,
    // and tell the user plainly instead of surfacing Google's raw OAuth error.
    await disconnect(account.email);
    throw new Error(`Google 연결이 만료됐어요 (${account.email}). 설정 → 구글 연결에서 "연결 해제" 후 다시 연결해 주세요.`);
  }
  const updated: StoredToken = {
    ...account,
    accessToken: refreshed.accessToken,
    refreshToken: refreshed.refreshToken ?? account.refreshToken,
    expiresIn: refreshed.expiresIn,
    issuedAt: refreshed.issuedAt,
  };
  await upsert(updated);
  return refreshed.accessToken;
}

async function api<T>(token: string, url: string, init: RequestInit = {}, reauthEmail = ''): Promise<T> {
  const call = (t: string) =>
    fetch(url, { ...init, headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json', ...init.headers } });
  let res = await call(token);
  // A token Google rejects (401) though our clock thinks it is fresh — force a new one and retry once.
  if (res.status === 401) res = await call(await freshToken(reauthEmail, true));
  if (!res.ok) throw new Error(`Google API 오류 ${res.status}: ${(await res.text()).slice(0, 300)}`);
  return res.json() as Promise<T>;
}

/** The token for an account email (blank = first account), optionally forcing a refresh (401 recovery). */
async function freshToken(email: string, force = false): Promise<string> {
  const accounts = await loadAccounts();
  const account = accounts.find((a) => a.email === email) ?? accounts[0];
  if (!account) throw new Error('Google 계정이 연결되어 있지 않아요. 설정에서 연결해 주세요.');
  return accessTokenFor(account, force);
}

/** Runs `fn` for every connected account (each with a fresh token) and flattens the results; an account that errors contributes nothing. */
async function perAccount<T>(fn: (account: StoredToken, token: string) => Promise<T[]>): Promise<T[]> {
  const accounts = await loadAccounts();
  const results = await Promise.all(
    accounts.map(async (a) => {
      try {
        return await fn(a, await accessTokenFor(a));
      } catch {
        return [] as T[];
      }
    }),
  );
  return results.flat();
}

/** Splits an id tagged as `email|~|realId` back into the account and the raw Google id. */
function routeId(id: string): { email: string; realId: string } {
  const i = id.indexOf(SEP);
  return i === -1 ? { email: '', realId: id } : { email: id.slice(0, i), realId: id.slice(i + SEP.length) };
}

/** The token for a specific account email, falling back to the first connected account. */
async function tokenForEmail(email: string): Promise<string> {
  return freshToken(email);
}

// ---------- Calendar ----------

// The owner lives/works on Philippine time; his Google calendar's own default is Europe/London,
// which made event times read wrong. Pin all calendar reads/writes to Manila so times are
// consistent regardless of the calendar's or the phone's timezone setting.
export const BIZ_TIMEZONE = 'Asia/Manila';

export type CalendarEvent = {
  id: string;
  title: string;
  start: string;
  end: string;
  allDay: boolean;
  location?: string;
  description?: string;
  /** Which connected Google account this event belongs to. */
  account?: string;
};

type RawEvent = {
  id: string;
  summary?: string;
  location?: string;
  description?: string;
  start: { dateTime?: string; date?: string };
  end: { dateTime?: string; date?: string };
};

const CAL = 'https://www.googleapis.com/calendar/v3/calendars/primary/events';

/** Events from every connected account, merged and sorted by start time. */
export async function listEvents(timeMin: Date, timeMax: Date, max = 50): Promise<CalendarEvent[]> {
  const params = new URLSearchParams({
    timeMin: timeMin.toISOString(),
    timeMax: timeMax.toISOString(),
    singleEvents: 'true',
    orderBy: 'startTime',
    maxResults: String(max),
    timeZone: BIZ_TIMEZONE,
  });
  const events = await perAccount(async (a, token) => {
    const data = await api<{ items?: RawEvent[] }>(token, `${CAL}?${params}`);
    return (data.items ?? []).map((e) => ({
      id: `${a.email}${SEP}${e.id}`,
      title: e.summary ?? '(제목 없음)',
      start: e.start.dateTime ?? e.start.date ?? '',
      end: e.end.dateTime ?? e.end.date ?? '',
      allDay: !e.start.dateTime,
      location: e.location,
      description: e.description?.slice(0, 500),
      account: a.email,
    }));
  });
  return events.sort((x, y) => x.start.localeCompare(y.start)).slice(0, max);
}

/** Creates an event. `account` picks the calendar; without it, the first connected account is used. */
export async function createEvent(input: { title: string; start: string; end: string; location?: string; description?: string; account?: string }) {
  const timeZone = BIZ_TIMEZONE;
  const token = await tokenForEmail(input.account ?? '');
  const e = await api<RawEvent & { htmlLink: string }>(token, CAL, {
    method: 'POST',
    body: JSON.stringify({
      summary: input.title,
      location: input.location,
      description: input.description,
      start: { dateTime: input.start, timeZone },
      end: { dateTime: input.end, timeZone },
    }),
  });
  return { id: e.id, link: e.htmlLink };
}

// ---------- Gmail ----------

const GMAIL = 'https://gmail.googleapis.com/gmail/v1/users/me';

export type MailSummary = { id: string; threadId: string; from: string; subject: string; date: string; snippet: string; unread: boolean; account?: string };

type RawPart = { mimeType: string; body?: { data?: string }; parts?: RawPart[]; headers?: { name: string; value: string }[] };
type RawMessage = { id: string; threadId: string; snippet: string; labelIds?: string[]; payload: RawPart };

const header = (m: RawMessage, name: string) => m.payload.headers?.find((h) => h.name.toLowerCase() === name.toLowerCase())?.value ?? '';

function decodeEntities(s: string) {
  return s.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
}

/** Searches every connected account, newest first. Ids are tagged with the account so readMail/createDraft can route back. */
export async function searchMail(query: string, max = 10): Promise<MailSummary[]> {
  const fields = new URLSearchParams({ format: 'metadata' });
  for (const h of ['From', 'Subject', 'Date']) fields.append('metadataHeaders', h);
  const mails = await perAccount(async (a, token) => {
    const list = await api<{ messages?: { id: string }[] }>(token, `${GMAIL}/messages?${new URLSearchParams({ q: query, maxResults: String(max) })}`);
    return Promise.all(
      (list.messages ?? []).map(async ({ id }) => {
        const m = await api<RawMessage>(token, `${GMAIL}/messages/${id}?${fields}`);
        return {
          id: `${a.email}${SEP}${m.id}`,
          threadId: `${a.email}${SEP}${m.threadId}`,
          from: header(m, 'From'),
          subject: header(m, 'Subject'),
          date: header(m, 'Date'),
          snippet: decodeEntities(m.snippet),
          unread: m.labelIds?.includes('UNREAD') ?? false,
          account: a.email,
        };
      }),
    );
  });
  return mails.sort((x, y) => (Date.parse(y.date) || 0) - (Date.parse(x.date) || 0));
}

function base64UrlToBytes(data: string): Uint8Array {
  const bin = atob(data.replace(/-/g, '+').replace(/_/g, '/'));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function utf8Decode(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; ) {
    const b = bytes[i];
    const len = b < 0x80 ? 1 : b < 0xe0 ? 2 : b < 0xf0 ? 3 : 4;
    let cp = len === 1 ? b : b & (0xff >> (len + 1));
    for (let k = 1; k < len; k++) cp = (cp << 6) | (bytes[i + k] & 0x3f);
    out += String.fromCodePoint(cp);
    i += len;
  }
  return out;
}

function utf8ToBase64Url(text: string): string {
  const bin = unescape(encodeURIComponent(text));
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function findBody(part: RawPart, mime: string): string | undefined {
  if (part.mimeType === mime && part.body?.data) return utf8Decode(base64UrlToBytes(part.body.data));
  for (const p of part.parts ?? []) {
    const found = findBody(p, mime);
    if (found) return found;
  }
  return undefined;
}

export async function readMail(id: string): Promise<MailSummary & { body: string }> {
  const { email, realId } = routeId(id);
  const token = await tokenForEmail(email);
  const m = await api<RawMessage>(token, `${GMAIL}/messages/${realId}?format=full`);
  const html = findBody(m.payload, 'text/html');
  const text =
    findBody(m.payload, 'text/plain') ??
    (html ? decodeEntities(html.replace(/<style[\s\S]*?<\/style>/gi, '').replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ') : m.snippet);
  return {
    id: `${email}${SEP}${m.id}`,
    threadId: `${email}${SEP}${m.threadId}`,
    from: header(m, 'From'),
    subject: header(m, 'Subject'),
    date: header(m, 'Date'),
    snippet: decodeEntities(m.snippet),
    unread: m.labelIds?.includes('UNREAD') ?? false,
    account: email || undefined,
    body: text.slice(0, 8000),
  };
}

/**
 * Saves a draft in Gmail; Dorothy never sends mail on her own, so the user reviews and sends it.
 * A reply (thread_id from searchMail/readMail) is drafted in that message's account; a brand-new
 * mail uses the first connected account.
 */
export async function createDraft(input: { to: string; subject: string; body: string; threadId?: string }) {
  const routed = input.threadId ? routeId(input.threadId) : { email: '', realId: undefined as string | undefined };
  const token = await tokenForEmail(routed.email);
  const subject = `=?UTF-8?B?${btoa(unescape(encodeURIComponent(input.subject)))}?=`;
  const raw = [`To: ${input.to}`, `Subject: ${subject}`, 'MIME-Version: 1.0', 'Content-Type: text/plain; charset=UTF-8', '', input.body].join('\r\n');
  const draft = await api<{ id: string }>(token, `${GMAIL}/drafts`, {
    method: 'POST',
    body: JSON.stringify({ message: { raw: utf8ToBase64Url(raw), threadId: routed.realId } }),
  });
  return { id: draft.id };
}

/** Actually sends an email. Only call after the user has clearly confirmed sending. */
export async function sendMail(input: { to: string; subject: string; body: string; threadId?: string }) {
  const routed = input.threadId ? routeId(input.threadId) : { email: '', realId: undefined as string | undefined };
  const token = await tokenForEmail(routed.email);
  const subject = `=?UTF-8?B?${btoa(unescape(encodeURIComponent(input.subject)))}?=`;
  const raw = [`To: ${input.to}`, `Subject: ${subject}`, 'MIME-Version: 1.0', 'Content-Type: text/plain; charset=UTF-8', '', input.body].join('\r\n');
  const sent = await api<{ id: string }>(token, `${GMAIL}/messages/send`, {
    method: 'POST',
    body: JSON.stringify({ raw: utf8ToBase64Url(raw), threadId: routed.realId }),
  });
  return { id: sent.id, from: routed.email || undefined };
}

// ---------- Drive (call-recording backup) ----------
const DRIVE = 'https://www.googleapis.com/drive/v3';
const DRIVE_UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,size,name';

/** Whether a Google account is connected at all (Drive uses the first one). */
export async function driveReady(): Promise<boolean> {
  return (await loadAccounts()).length > 0;
}

const folderCache: Record<string, string> = {};

/** Finds (or creates) a Drive folder of the given name that this app owns, returning its id. */
export async function ensureDriveFolder(name: string): Promise<string> {
  if (folderCache[name]) return folderCache[name];
  const token = await tokenForEmail('');
  const q = encodeURIComponent(`name='${name.replace(/'/g, "\\'")}' and mimeType='application/vnd.google-apps.folder' and trashed=false`);
  const found = await api<{ files: { id: string }[] }>(token, `${DRIVE}/files?q=${q}&spaces=drive&fields=files(id)`);
  if (found.files?.[0]?.id) return (folderCache[name] = found.files[0].id);
  const created = await api<{ id: string }>(token, `${DRIVE}/files?fields=id`, {
    method: 'POST',
    body: JSON.stringify({ name, mimeType: 'application/vnd.google-apps.folder' }),
  });
  return (folderCache[name] = created.id);
}

/**
 * Uploads one file (given as base64) into a Drive folder via a multipart/related request.
 * Returns the new file's id and the byte size Drive stored, so the caller can verify before
 * deleting the local original.
 */
export async function uploadToDrive(input: {
  folderId: string;
  name: string;
  mimeType: string;
  base64: string;
}): Promise<{ id: string; size: number; name: string }> {
  const boundary = 'dorothy-' + Math.random().toString(36).slice(2);
  const metadata = { name: input.name, parents: [input.folderId] };
  const body =
    `--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${JSON.stringify(metadata)}\r\n` +
    `--${boundary}\r\nContent-Type: ${input.mimeType}\r\nContent-Transfer-Encoding: base64\r\n\r\n${input.base64}\r\n` +
    `--${boundary}--`;
  const send = (t: string) =>
    fetch(DRIVE_UPLOAD, {
      method: 'POST',
      headers: { Authorization: `Bearer ${t}`, 'Content-Type': `multipart/related; boundary=${boundary}` },
      body,
    });
  let res = await send(await tokenForEmail(''));
  // Token rejected mid-backup (e.g. expired after an earlier file) → refresh once and retry.
  if (res.status === 401) res = await send(await freshToken('', true));
  const text = await res.text();
  if (res.status === 403) {
    throw new Error('구글 드라이브 권한이 없어요. 설정에서 구글을 다시 연결해 드라이브 권한을 허용해 주세요.');
  }
  if (!res.ok) throw new Error(`드라이브 업로드 오류 ${res.status}: ${text.slice(0, 200)}`);
  const json = JSON.parse(text) as { id: string; size?: string; name: string };
  return { id: json.id, size: Number(json.size ?? 0), name: json.name };
}

// ---------- Sheets (read-only) ----------
const SHEETS = 'https://sheets.googleapis.com/v4/spreadsheets';

/** Pulls the spreadsheet id out of a full Google Sheets link (or returns the input if it is already an id). */
export function parseSpreadsheetId(link: string): string {
  const m = /\/spreadsheets\/d\/([a-zA-Z0-9-_]+)/.exec(link || '');
  return (m ? m[1] : (link || '').trim());
}

/** Spreadsheet title and the names of its tabs (sheets). */
export async function getSpreadsheetMeta(id: string): Promise<{ title: string; tabs: string[] }> {
  const token = await tokenForEmail('');
  const data = await api<{ properties?: { title?: string }; sheets?: { properties?: { title?: string } }[] }>(
    token,
    `${SHEETS}/${encodeURIComponent(id)}?fields=${encodeURIComponent('properties.title,sheets.properties.title')}`,
  );
  return {
    title: data.properties?.title ?? '(제목 없음)',
    tabs: (data.sheets ?? []).map((s) => s.properties?.title ?? '').filter(Boolean),
  };
}

/** Reads the cell values of one tab, capped to maxRows. */
export async function readSheetValues(id: string, tab: string, maxRows = 200): Promise<string[][]> {
  const token = await tokenForEmail('');
  const data = await api<{ values?: string[][] }>(
    token,
    `${SHEETS}/${encodeURIComponent(id)}/values/${encodeURIComponent(tab)}?majorDimension=ROWS`,
  );
  return (data.values ?? []).slice(0, maxRows);
}

/** Lists audio files inside a Drive folder the app owns (drive.file sees files the app created). */
export async function listDriveFolderAudio(folderId: string): Promise<{ id: string; name: string; size: number; mimeType: string }[]> {
  const token = await tokenForEmail('');
  const q = encodeURIComponent(`'${folderId}' in parents and trashed=false and mimeType contains 'audio/'`);
  const out: { id: string; name: string; size: number; mimeType: string }[] = [];
  let pageToken = '';
  do {
    const url = `${DRIVE}/files?q=${q}&spaces=drive&fields=nextPageToken,files(id,name,size,mimeType)&pageSize=200${pageToken ? `&pageToken=${pageToken}` : ''}`;
    const data = await api<{ nextPageToken?: string; files?: { id: string; name: string; size?: string; mimeType: string }[] }>(token, url);
    for (const f of data.files ?? []) out.push({ id: f.id, name: f.name, size: Number(f.size ?? 0), mimeType: f.mimeType });
    pageToken = data.nextPageToken ?? '';
  } while (pageToken);
  return out;
}

/** Downloads a Drive file to a temp path and returns its base64 (used to feed audio to Gemini). */
export async function downloadDriveFileBase64(fileId: string): Promise<string> {
  const dest = `${FileSystem.cacheDirectory}drive-dl.tmp`;
  const dl = (t: string) =>
    FileSystem.downloadAsync(`${DRIVE}/files/${fileId}?alt=media`, dest, { headers: { Authorization: `Bearer ${t}` } });
  let r = await dl(await tokenForEmail(''));
  if (r.status === 401) r = await dl(await freshToken('', true)); // refresh once and retry
  if (r.status !== 200) throw new Error(`드라이브 다운로드 오류 ${r.status}`);
  const b64 = await FileSystem.readAsStringAsync(dest, { encoding: FileSystem.EncodingType.Base64 });
  await FileSystem.deleteAsync(dest, { idempotent: true }).catch(() => {});
  return b64;
}

/**
 * Downloads a Drive file to a temp path and returns its local URI (caller deletes it when done).
 * Used to transcribe a Drive recording on-device without keeping it on the phone.
 */
export async function downloadDriveFileToTemp(fileId: string, ext = 'm4a'): Promise<string> {
  const dest = `${FileSystem.cacheDirectory}drive-rec.${ext}`;
  const dl = (t: string) =>
    FileSystem.downloadAsync(`${DRIVE}/files/${fileId}?alt=media`, dest, { headers: { Authorization: `Bearer ${t}` } });
  let r = await dl(await tokenForEmail(''));
  if (r.status === 401) r = await dl(await freshToken('', true));
  if (r.status !== 200) throw new Error(`드라이브 다운로드 오류 ${r.status}`);
  return dest;
}
