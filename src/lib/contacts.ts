import * as Contacts from 'expo-contacts';
import { Platform } from 'react-native';

/**
 * The phone's contacts, used to turn a bare number (from an SMS or call log) into a name —
 * "김부장님한테 부재중" instead of "010-1234-5678한테 부재중". Read on demand and cached in memory
 * for the session; never sent anywhere except, with the rest of a summary, to Dorothy's brain when asked.
 */
export type Contact = { name: string; numbers: string[] };

let cache: Contact[] | null = null;
let numberMap: Map<string, string> | null = null;

/** Last 10 digits, so +82 10-1234-5678 and 010-1234-5678 match. */
function normalize(raw: string): string {
  const digits = (raw || '').replace(/\D/g, '');
  return digits.length > 10 ? digits.slice(-10) : digits;
}

export async function contactsGranted(): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  const { status } = await Contacts.getPermissionsAsync();
  return status === 'granted';
}

export async function requestContacts(): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  const { status } = await Contacts.requestPermissionsAsync();
  return status === 'granted';
}

/** Loads all contacts with phone numbers once and caches them. Empty when not permitted. */
export async function loadContacts(force = false): Promise<Contact[]> {
  if (cache && !force) return cache;
  if (!(await contactsGranted())) return (cache = []);
  const out: Contact[] = [];
  let offset = 0;
  // Page through so very large address books all load.
  for (let guard = 0; guard < 200; guard++) {
    const { data, hasNextPage } = await Contacts.getContactsAsync({
      fields: [Contacts.Fields.PhoneNumbers],
      pageSize: 500,
      pageOffset: offset,
    });
    for (const c of data) {
      const numbers = (c.phoneNumbers ?? []).map((p) => p.number ?? '').filter(Boolean);
      if (c.name && numbers.length) out.push({ name: c.name, numbers });
    }
    if (!hasNextPage || !data.length) break;
    offset += data.length;
  }
  cache = out;
  numberMap = new Map();
  for (const c of out) for (const n of c.numbers) numberMap.set(normalize(n), c.name);
  return out;
}

/** The contact name for a number, or null. Call loadContacts() first (e.g. once before formatting a batch). */
export function nameFor(number?: string): string | null {
  if (!number || !numberMap) return null;
  return numberMap.get(normalize(number)) ?? null;
}

/** Prefixes "이름 (번호)" when the number is known, else the number alone. */
export function label(number?: string): string {
  const name = nameFor(number);
  return name ? `${name} (${number})` : number || '번호 없음';
}

/** Searches contacts by name or number, for Dorothy's "X 번호 뭐야?" lookups. */
export async function searchContacts(query: string, limit = 20): Promise<Contact[]> {
  const all = await loadContacts();
  const q = query.trim().toLowerCase();
  if (!q) return all.slice(0, limit);
  const qDigits = q.replace(/\D/g, '');
  return all
    .filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        (qDigits && c.numbers.some((n) => n.replace(/\D/g, '').includes(qDigits))),
    )
    .slice(0, limit);
}

export function formatContacts(list: Contact[]): string {
  return list.map((c) => `- ${c.name}: ${c.numbers.join(', ')}`).join('\n');
}
