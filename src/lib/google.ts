import * as AuthSession from 'expo-auth-session';
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

async function accessTokenFor(account: StoredToken): Promise<string> {
  if (AuthSession.TokenResponse.isTokenFresh(account, 60)) return account.accessToken;
  if (!account.refreshToken) {
    await disconnect(account.email);
    throw new Error(`Google 로그인이 만료됐어요 (${account.email}). 설정에서 다시 연결해 주세요.`);
  }
  const refreshed = await AuthSession.refreshAsync({ clientId: googleClientId(), refreshToken: account.refreshToken }, discovery);
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

async function api<T>(token: string, url: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...init.headers },
  });
  if (!res.ok) throw new Error(`Google API 오류 ${res.status}: ${(await res.text()).slice(0, 300)}`);
  return res.json() as Promise<T>;
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
  const accounts = await loadAccounts();
  const account = accounts.find((a) => a.email === email) ?? accounts[0];
  if (!account) throw new Error('Google 계정이 연결되어 있지 않아요. 설정에서 연결해 주세요.');
  return accessTokenFor(account);
}

// ---------- Calendar ----------

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
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
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
