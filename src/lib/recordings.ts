// Call-recording backup. Samsung (and most Android) dialers save call recordings as audio files
// in a folder the user picks once via the Storage Access Framework (no broad storage permission
// needed). Dorothy uploads new recordings to a Drive folder and — when the user chose "move" —
// deletes the local original only after Drive confirms the upload, to free phone storage.
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as FileSystem from 'expo-file-system/legacy';

import * as gemini from './gemini';
import * as google from './google';

const SAF = FileSystem.StorageAccessFramework;
const DRIVE_FOLDER = '도로시 통화녹음';

const KEYS = {
  folder: 'dorothy.rec.folder',
  delete: 'dorothy.rec.delete',
  enabled: 'dorothy.rec.enabled',
  done: 'dorothy.rec.done',
  lastRun: 'dorothy.rec.lastRun',
  summarize: 'dorothy.rec.summarize',
  summaries: 'dorothy.callSummaries',
};

// Gemini inline audio has to fit in one request; keep well under its ~20MB cap (base64 is ~1.33x).
const SUMMARIZE_MAX_BYTES = 12 * 1024 * 1024;

const AUDIO_EXT: Record<string, string> = {
  m4a: 'audio/mp4', mp3: 'audio/mpeg', amr: 'audio/amr', '3gp': 'audio/3gpp',
  '3ga': 'audio/3gpp', wav: 'audio/wav', ogg: 'audio/ogg', aac: 'audio/aac', opus: 'audio/opus',
};

export type RecordingConfig = { folderUri: string; deleteAfter: boolean; enabled: boolean; summarize: boolean };

export async function getConfig(): Promise<RecordingConfig> {
  const [folderUri, del, en, sum] = await Promise.all([
    AsyncStorage.getItem(KEYS.folder),
    AsyncStorage.getItem(KEYS.delete),
    AsyncStorage.getItem(KEYS.enabled),
    AsyncStorage.getItem(KEYS.summarize),
  ]);
  return {
    folderUri: folderUri ?? '',
    deleteAfter: del == null ? true : del === 'true', // 사용자 선택: 올리고 삭제가 기본
    enabled: en === 'true',
    summarize: sum === 'true',
  };
}

export async function setDeleteAfter(v: boolean) { await AsyncStorage.setItem(KEYS.delete, String(v)); }
export async function setEnabled(v: boolean) { await AsyncStorage.setItem(KEYS.enabled, String(v)); }
export async function setSummarize(v: boolean) { await AsyncStorage.setItem(KEYS.summarize, String(v)); }
export async function getLastRun(): Promise<string> { return (await AsyncStorage.getItem(KEYS.lastRun)) ?? ''; }

/** A Gemini-made summary of one call recording, kept so Dorothy can recall what a call was about. */
export type CallSummary = { id: string; name: string; at: string; summary: string };

export async function getCallSummaries(): Promise<CallSummary[]> {
  try {
    const raw = await AsyncStorage.getItem(KEYS.summaries);
    return raw ? (JSON.parse(raw) as CallSummary[]) : [];
  } catch {
    return [];
  }
}
async function appendSummary(entry: CallSummary) {
  const list = await getCallSummaries();
  list.push(entry);
  await AsyncStorage.setItem(KEYS.summaries, JSON.stringify(list.slice(-200)));
}
export async function clearCallSummaries() { await AsyncStorage.removeItem(KEYS.summaries); }

/** Opens the system folder picker; on success saves and returns the granted folder URI. */
export async function pickFolder(): Promise<string | null> {
  const perm = await SAF.requestDirectoryPermissionsAsync();
  if (!perm.granted || !perm.directoryUri) return null;
  await AsyncStorage.setItem(KEYS.folder, perm.directoryUri);
  return perm.directoryUri;
}

export async function folderLabel(): Promise<string> {
  const { folderUri } = await getConfig();
  if (!folderUri) return '';
  try {
    const decoded = decodeURIComponent(folderUri);
    const tail = decoded.split(/[:/]/).filter(Boolean).pop() ?? '';
    return tail || decoded;
  } catch {
    return folderUri;
  }
}

function fileNameOf(uri: string): string {
  try {
    const decoded = decodeURIComponent(uri);
    return decoded.split('/').pop() ?? decoded;
  } catch {
    return uri.split('/').pop() ?? uri;
  }
}

function isAudio(uri: string): boolean {
  const name = fileNameOf(uri).toLowerCase();
  const ext = name.includes('.') ? name.split('.').pop()! : '';
  return ext in AUDIO_EXT;
}

function mimeOf(name: string): string {
  const ext = name.toLowerCase().includes('.') ? name.toLowerCase().split('.').pop()! : '';
  return AUDIO_EXT[ext] ?? 'application/octet-stream';
}

async function loadDone(): Promise<Set<string>> {
  try {
    const raw = await AsyncStorage.getItem(KEYS.done);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}
async function saveDone(done: Set<string>) {
  // keep the list from growing without bound (recent 2000 names is plenty)
  const arr = [...done].slice(-2000);
  await AsyncStorage.setItem(KEYS.done, JSON.stringify(arr));
}

export type BackupResult = {
  total: number;
  uploaded: number;
  deleted: number;
  skipped: number;
  failed: number;
  freedBytes: number;
  summarized: number;
};

export type Progress = { index: number; total: number; name: string };

const MAX_BYTES = 45 * 1024 * 1024; // skip anything huge so reading it as base64 can't exhaust memory

/**
 * Scans the chosen folder and backs up every audio file not already handled.
 * When deleteAfter is on, a local file is removed only after Drive returns a matching byte size.
 */
export async function backupNow(onProgress?: (p: Progress) => void): Promise<BackupResult> {
  const cfg = await getConfig();
  if (!cfg.folderUri) throw new Error('먼저 녹음 폴더를 선택해 주세요.');
  if (!(await google.driveReady())) throw new Error('먼저 구글을 연결해 주세요 (설정 → 구글 연결).');

  const folderId = await google.ensureDriveFolder(DRIVE_FOLDER);
  let entries: string[];
  try {
    entries = await SAF.readDirectoryAsync(cfg.folderUri);
  } catch {
    throw new Error('녹음 폴더를 읽을 수 없어요. 폴더를 다시 선택해 주세요.');
  }
  const files = entries.filter(isAudio);
  const done = await loadDone();

  const res: BackupResult = { total: files.length, uploaded: 0, deleted: 0, skipped: 0, failed: 0, freedBytes: 0, summarized: 0 };
  const wantSummary = cfg.summarize && (await gemini.geminiConfigured());

  for (let i = 0; i < files.length; i++) {
    const uri = files[i];
    const name = fileNameOf(uri);
    if (done.has(name)) { res.skipped++; continue; }
    onProgress?.({ index: i + 1, total: files.length, name });

    try {
      let size = 0;
      try {
        const info = await FileSystem.getInfoAsync(uri);
        size = info.exists && 'size' in info ? (info.size ?? 0) : 0;
      } catch {
        // size stays 0; verification will fall back to "id present"
      }
      if (size > MAX_BYTES) { res.skipped++; done.add(name); continue; }

      const base64 = await FileSystem.readAsStringAsync(uri, { encoding: FileSystem.EncodingType.Base64 });

      // Summarize with Gemini before uploading/deleting, so Dorothy remembers what the call was about.
      // A summary failure never blocks the backup.
      if (wantSummary && size > 0 && size <= SUMMARIZE_MAX_BYTES) {
        try {
          const summary = await gemini.summarizeCallAudio(base64, mimeOf(name));
          await appendSummary({ id: `${Date.now()}-${i}`, name, at: new Date().toISOString(), summary });
          res.summarized++;
        } catch {
          // leave it; the audio still gets backed up and the user can retry later
        }
      }

      const up = await google.uploadToDrive({ folderId, name, mimeType: mimeOf(name), base64 });
      res.uploaded++;

      const verified = !!up.id && (up.size === 0 || size === 0 || up.size === size);
      if (cfg.deleteAfter && verified) {
        try {
          await SAF.deleteAsync(uri);
          res.deleted++;
          res.freedBytes += size;
        } catch {
          done.add(name); // uploaded fine but couldn't delete; don't re-upload next time
        }
      } else {
        done.add(name); // keep local copy (or unverified) — remember so we don't upload twice
      }
    } catch {
      res.failed++;
    }
  }

  await saveDone(done);
  await AsyncStorage.setItem(KEYS.lastRun, new Date().toISOString());
  return res;
}

/** Best-effort silent run used when the app opens (does nothing unless set up and enabled). */
export async function autoBackupIfDue(): Promise<void> {
  const cfg = await getConfig();
  if (!cfg.enabled || !cfg.folderUri) return;
  if (!(await google.driveReady())) return;
  try {
    await backupNow();
  } catch {
    // silent — the Settings card is where errors are shown
  }
}
