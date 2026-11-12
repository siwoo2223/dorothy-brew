// Google Sheets the owner points Dorothy at. He pastes a sheet link in Settings once; the id and
// title are saved on the device, and the assistant can then read any tab of those sheets read-only
// (e.g. '2026 리스트', '지출'). We keep a short saved list rather than browsing all of Drive, so the
// Google scope stays minimal (spreadsheets.readonly) and Dorothy only sees sheets he chose.
import AsyncStorage from '@react-native-async-storage/async-storage';

import * as google from './google';

const KEY = 'dorothy.sheets';

export type SavedSheet = { id: string; title: string };

export async function listSheets(): Promise<SavedSheet[]> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as SavedSheet[]) : [];
  } catch {
    return [];
  }
}

async function save(list: SavedSheet[]) {
  await AsyncStorage.setItem(KEY, JSON.stringify(list));
}

/** Adds a sheet from its link: resolves the id, fetches the title, stores it (deduped by id). */
export async function addSheet(link: string): Promise<SavedSheet> {
  const id = google.parseSpreadsheetId(link);
  if (!id) throw new Error('구글 시트 링크를 알아보지 못했어요. 시트 주소를 그대로 붙여넣어 주세요.');
  const meta = await google.getSpreadsheetMeta(id); // also verifies access with the current token
  const sheet: SavedSheet = { id, title: meta.title };
  const list = (await listSheets()).filter((s) => s.id !== id);
  await save([...list, sheet]);
  return sheet;
}

export async function removeSheet(id: string): Promise<void> {
  await save((await listSheets()).filter((s) => s.id !== id));
}

/**
 * Reads a saved sheet for the assistant. `name` picks the spreadsheet (title contains it); with one
 * saved sheet it is optional. `tab` picks the tab — left empty, it returns the list of tabs to choose.
 * Returns plain text for Claude to read.
 */
export async function readForAssistant(name: string | undefined, tab: string | undefined): Promise<string> {
  const saved = await listSheets();
  if (!saved.length) return '저장된 구글 시트가 없어요. 설정 → "구글 시트"에서 시트 링크를 먼저 추가해 주세요.';

  let sheet = saved.length === 1 ? saved[0] : undefined;
  if (name) {
    const n = name.trim();
    sheet = saved.find((s) => s.title === n) ?? saved.find((s) => s.title.includes(n) || n.includes(s.title)) ?? sheet;
  }
  if (!sheet) return `어느 시트인지 모르겠어요. 저장된 시트: ${saved.map((s) => s.title).join(', ')}. 이름을 알려 주세요.`;

  const meta = await google.getSpreadsheetMeta(sheet.id);
  if (!tab || !tab.trim()) {
    return `시트 "${meta.title}"의 탭 목록: ${meta.tabs.join(', ') || '(없음)'}. 어느 탭을 볼지 알려 주세요.`;
  }
  const want = tab.trim();
  const realTab = meta.tabs.find((t) => t === want) ?? meta.tabs.find((t) => t.includes(want) || want.includes(t)) ?? want;
  const rows = await google.readSheetValues(sheet.id, realTab);
  if (!rows.length) return `"${meta.title}" / "${realTab}" 탭에 데이터가 없어요.`;
  const body = rows.map((r) => r.join(' | ')).join('\n');
  return `시트 "${meta.title}" / 탭 "${realTab}" (${rows.length}행, 위에서부터):\n${body}`;
}
