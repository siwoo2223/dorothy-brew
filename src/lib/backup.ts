import AsyncStorage from '@react-native-async-storage/async-storage';
import * as FileSystem from 'expo-file-system/legacy';

import * as google from './google';
import type { AppState } from './store';

/**
 * 도로시 백업: 사장님의 기억·할 일·설정을 구글 드라이브의 '도로시 백업' 폴더에 JSON으로 올린다.
 * 폰을 잃어버리거나 바꿔도 복구할 수 있게. API 키·회사 토큰 같은 비밀은 백업에 넣지 않는다(보안).
 * 드라이브는 이미 통화 녹음 백업에 쓰는 것과 같은 권한(앱이 만든 파일만)을 쓴다.
 */

const FOLDER = '도로시 백업';
const PREFIX = 'dorothy-backup-';

export type Backup = {
  version: 1;
  savedAt: string;
  profile: AppState['profile'];
  memories: AppState['memories'];
  tasks: AppState['tasks'];
  followups: AppState['followups'];
  routines: AppState['routines'];
  sailings: AppState['sailings'];
  expenses: AppState['expenses'];
  todoRooms: string[];
  fxRooms: string[];
  settings: {
    model: AppState['model'];
    briefingHour: AppState['briefingHour'];
    planHour: AppState['planHour'];
    weeklyReportDay: AppState['weeklyReportDay'];
    speechRate: AppState['speechRate'];
    voiceId: AppState['voiceId'];
    voiceOnOpen: AppState['voiceOnOpen'];
    companyUrl: AppState['companyUrl'];
  };
};

/** 백업에 담을 안전한 데이터만 추린다(비밀·대화기록 제외). */
export function buildBackup(s: AppState): Backup {
  return {
    version: 1,
    savedAt: new Date().toISOString(),
    profile: s.profile,
    memories: s.memories ?? [],
    tasks: s.tasks ?? [],
    followups: s.followups ?? [],
    routines: s.routines ?? [],
    sailings: s.sailings ?? [],
    expenses: s.expenses ?? [],
    todoRooms: s.todoRooms ?? [],
    fxRooms: s.fxRooms ?? [],
    settings: {
      model: s.model,
      briefingHour: s.briefingHour,
      planHour: s.planHour,
      weeklyReportDay: s.weeklyReportDay,
      speechRate: s.speechRate,
      voiceId: s.voiceId,
      voiceOnOpen: s.voiceOnOpen,
      companyUrl: s.companyUrl,
    },
  };
}

/** 구글이 연결돼 있고 드라이브 권한이 있는지. */
export async function backupReady(): Promise<boolean> {
  return google.driveReady();
}

/** 백업을 드라이브에 올린다. 반환: 파일 이름·바이트 크기. */
export async function backupToDrive(s: AppState): Promise<{ name: string; size: number }> {
  const json = JSON.stringify(buildBackup(s));
  const now = new Date();
  const stamp =
    now.getFullYear().toString() +
    String(now.getMonth() + 1).padStart(2, '0') +
    String(now.getDate()).padStart(2, '0') +
    '-' +
    String(now.getHours()).padStart(2, '0') +
    String(now.getMinutes()).padStart(2, '0');
  // UTF-8(한글) → base64 는 btoa 로 안 되므로 임시 파일을 거쳐 base64 로 읽는다.
  const tmp = `${FileSystem.cacheDirectory}dorothy-backup.json`;
  await FileSystem.writeAsStringAsync(tmp, json);
  const base64 = await FileSystem.readAsStringAsync(tmp, { encoding: FileSystem.EncodingType.Base64 });
  await FileSystem.deleteAsync(tmp, { idempotent: true }).catch(() => {});
  const folderId = await google.ensureDriveFolder(FOLDER);
  const res = await google.uploadToDrive({ folderId, name: `${PREFIX}${stamp}.json`, mimeType: 'application/json', base64 });
  return { name: res.name, size: res.size };
}

/** 하루에 한 번만 자동 백업한다(구글 연결돼 있을 때). 실패해도 조용히 넘어간다. */
export async function autoBackupIfDue(s: AppState): Promise<void> {
  try {
    if (!(await google.driveReady())) return;
    const today = new Date().toISOString().slice(0, 10);
    const last = await AsyncStorage.getItem('dorothy.backupDay');
    if (last === today) return;
    await backupToDrive(s);
    await AsyncStorage.setItem('dorothy.backupDay', today);
  } catch {
    // best-effort: 다음 기회에 다시 시도
  }
}

/** 드라이브 '도로시 백업' 폴더의 가장 최근 백업 파일. 없으면 null. */
export async function latestBackup(): Promise<{ id: string; name: string } | null> {
  const folderId = await google.ensureDriveFolder(FOLDER);
  const files = await google.listDriveFolderAudio(folderId); // drive.file: 폴더 안 파일 목록(오디오 전용 아님, 전체 반환)
  const backups = (files ?? [])
    .filter((f) => f.name.startsWith(PREFIX) && f.name.endsWith('.json'))
    .sort((a, b) => b.name.localeCompare(a.name));
  return backups[0] ? { id: backups[0].id, name: backups[0].name } : null;
}

/** 가장 최근 백업을 내려받아 파싱한다. 없거나 깨졌으면 null. */
export async function restoreFromDrive(): Promise<Backup | null> {
  const latest = await latestBackup();
  if (!latest) return null;
  const uri = await google.downloadDriveFileToTemp(latest.id, 'json');
  try {
    const text = await FileSystem.readAsStringAsync(uri);
    const data = JSON.parse(text);
    return data && typeof data === 'object' ? (data as Backup) : null;
  } catch {
    return null;
  } finally {
    await FileSystem.deleteAsync(uri, { idempotent: true }).catch(() => {});
  }
}
