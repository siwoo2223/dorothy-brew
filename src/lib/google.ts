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
  return Platform.OS === 'ios' ? CLIENT_IDS.ios : Platform.OS === 'android' ? CLIENT_IDS.android : '';
}

/** Google redirects native clients to the reversed client ID scheme, which app.config.ts registers. */
export function googleRedirectUri(): string {
  const id = googleClientId();
  const reversed = id.split('.').reverse().join('.');
  return `${reversed}:/oauthredirect`;
}

type StoredToken = { accessToken: string; refreshToken?: string; expiresIn?: number; issuedAt: number; email?: string };
const TOKEN_KEY = 'dorothy.googleToken';

let cached: StoredToken | null | undefined;

async function loadToken(): Promise<StoredToken | null> {
  if (cached !== undefined) return cached;
  const raw = Platform.OS === 'web' ? null : await SecureStore.getItemAsync(TOKEN_KEY);
  cached = raw ? JSON.parse(raw) : null;
  return cached ?? null;
}

async function saveToken(token: StoredToken | null) {
  cached = token;
  if (Platform.OS === 'web') return;
  if (token) await SecureStore.setItemAsync(TOKEN_KEY, JSON.stringify(token));
  else await SecureStore.deleteItemAsync(TOKEN_KEY);
}

export async function connectedEmail(): Promise<string | null> {
  const token = await loadToken();
  return token ? token.email ?? '연결됨' : null;
}

/** Finishes sign-in after the auth prompt returns a code. Returns the signed-in email. */
export async function completeSignIn(request: AuthSession.AuthRequest, code: string): Promise<string> {
  const response = await AuthSession.exchangeCodeAsync(
    {
      clientId: googleClientId(),
      code,
      redirectUri: googleRedirectUri(),
      extraParams: { code_verifier: request.codeVerifier ?? '' },
    },
    discovery,
  );
  const info = await AuthSession.fetchUserInfoAsync({ accessToken: response.accessToken }, discovery);
  await saveToken({
    accessToken: response.accessToken,
    refreshToken: response.refreshToken,
    expiresIn: response.expiresIn,
    issuedAt: response.issuedAt,
    email: info.email,
  });
  return info.email ?? '';
}

export async function disconnect(): Promise<void> {
  const token = await loadToken();
  if (token) {
    await AuthSession.revokeAsync({ token: token.refreshToken ?? token.accessToken, clientId: googleClientId() }, discovery).catch(() => {});
  }
  await saveToken(null);
}

async function accessToken(): Promise<string> {
  const token = await loadToken();
  if (!token) throw new Error('Google 계정이 연결되어 있지 않아요. 설정에서 연결해 주세요.');
  if (AuthSession.TokenResponse.isTokenFresh(token, 60)) return token.accessToken;
  if (!token.refreshToken) {
    await saveToken(null);
    throw new Error('Google 로그인이 만료됐어요. 설정에서 다시 연결해 주세요.');
  }
  const refreshed = await AuthSession.refreshAsync({ clientId: googleClientId(), refreshToken: token.refreshToken }, discovery);
  await saveToken({
    ...token,
    accessToken: refreshed.accessToken,
    refreshToken: refreshed.refreshToken ?? token.refreshToken,
    expiresIn: refreshed.expiresIn,
    issuedAt: refreshed.issuedAt,
  });
  return refreshed.accessToken;
}

async function api<T>(url: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { Authorization: `Bearer ${await accessToken()}`, 'Content-Type': 'application/json', ...init.headers },
  });
  if (!res.ok) throw new Error(`Google API 오류 ${res.status}: ${(await res.text()).slice(0, 300)}`);
  return res.json() as Promise<T>;
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

export async function listEvents(timeMin: Date, timeMax: Date, max = 50): Promise<CalendarEvent[]> {
  const params = new URLSearchParams({
    timeMin: timeMin.toISOString(),
    timeMax: timeMax.toISOString(),
    singleEvents: 'true',
    orderBy: 'startTime',
    maxResults: String(max),
  });
  const data = await api<{ items?: RawEvent[] }>(`${CAL}?${params}`);
  return (data.items ?? []).map((e) => ({
    id: e.id,
    title: e.summary ?? '(제목 없음)',
    start: e.start.dateTime ?? e.start.date ?? '',
    end: e.end.dateTime ?? e.end.date ?? '',
    allDay: !e.start.dateTime,
    location: e.location,
    description: e.description?.slice(0, 500),
  }));
}

export async function createEvent(input: { title: string; start: string; end: string; location?: string; description?: string }) {
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const e = await api<RawEvent & { htmlLink: string }>(CAL, {
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

export type MailSummary = { id: string; threadId: string; from: string; subject: string; date: string; snippet: string; unread: boolean };

type RawPart = { mimeType: string; body?: { data?: string }; parts?: RawPart[]; headers?: { name: string; value: string }[] };
type RawMessage = { id: string; threadId: string; snippet: string; labelIds?: string[]; payload: RawPart };

const header = (m: RawMessage, name: string) => m.payload.headers?.find((h) => h.name.toLowerCase() === name.toLowerCase())?.value ?? '';

function decodeEntities(s: string) {
  return s.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
}

export async function searchMail(query: string, max = 10): Promise<MailSummary[]> {
  const list = await api<{ messages?: { id: string }[] }>(`${GMAIL}/messages?${new URLSearchParams({ q: query, maxResults: String(max) })}`);
  const fields = new URLSearchParams({ format: 'metadata' });
  for (const h of ['From', 'Subject', 'Date']) fields.append('metadataHeaders', h);
  return Promise.all(
    (list.messages ?? []).map(async ({ id }) => {
      const m = await api<RawMessage>(`${GMAIL}/messages/${id}?${fields}`);
      return {
        id: m.id,
        threadId: m.threadId,
        from: header(m, 'From'),
        subject: header(m, 'Subject'),
        date: header(m, 'Date'),
        snippet: decodeEntities(m.snippet),
        unread: m.labelIds?.includes('UNREAD') ?? false,
      };
    }),
  );
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
  const m = await api<RawMessage>(`${GMAIL}/messages/${id}?format=full`);
  const html = findBody(m.payload, 'text/html');
  const text =
    findBody(m.payload, 'text/plain') ??
    (html ? decodeEntities(html.replace(/<style[\s\S]*?<\/style>/gi, '').replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ') : m.snippet);
  return {
    id: m.id,
    threadId: m.threadId,
    from: header(m, 'From'),
    subject: header(m, 'Subject'),
    date: header(m, 'Date'),
    snippet: decodeEntities(m.snippet),
    unread: m.labelIds?.includes('UNREAD') ?? false,
    body: text.slice(0, 8000),
  };
}

/** Saves a draft in Gmail; Dorothy never sends mail on her own, so the user reviews and sends it. */
export async function createDraft(input: { to: string; subject: string; body: string; threadId?: string }) {
  const subject = `=?UTF-8?B?${btoa(unescape(encodeURIComponent(input.subject)))}?=`;
  const raw = [`To: ${input.to}`, `Subject: ${subject}`, 'MIME-Version: 1.0', 'Content-Type: text/plain; charset=UTF-8', '', input.body].join('\r\n');
  const draft = await api<{ id: string }>(`${GMAIL}/drafts`, {
    method: 'POST',
    body: JSON.stringify({ message: { raw: utf8ToBase64Url(raw), threadId: input.threadId } }),
  });
  return { id: draft.id };
}
