// In-app updater. The app is distributed as a signed APK published to GitHub Releases
// by .github/workflows/android-apk.yml (tag "build-<run_number>"). Each build bakes its own
// run number in via EXPO_PUBLIC_BUILD_NUMBER, so the app can compare itself to the latest
// release and, when newer, download the APK and hand it to Android's package installer —
// no more "open GitHub, download, install" by hand.
import * as FileSystem from 'expo-file-system/legacy';
import * as IntentLauncher from 'expo-intent-launcher';
import { Platform } from 'react-native';

const REPO = 'siwoo2223/dorothy-brew';
const LATEST_RELEASE_API = `https://api.github.com/repos/${REPO}/releases/latest`;
export const RELEASES_PAGE = `https://github.com/${REPO}/releases/latest`;

/** The build this running app was produced from (0 in dev / unsigned local runs). */
export const CURRENT_BUILD = Number(process.env.EXPO_PUBLIC_BUILD_NUMBER) || 0;

export type UpdateInfo = {
  build: number;
  tag: string;
  name: string;
  url: string;
  notes: string;
};

export type UpdateCheck = {
  available: boolean;
  current: number;
  latest: UpdateInfo | null;
};

function parseBuild(tag: string): number {
  const m = /build-(\d+)/.exec(tag ?? '');
  return m ? Number(m[1]) : 0;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/**
 * GitHub에 최신 릴리스를 묻되, 일시적 오류(요청 타임아웃, 502/503/504, 네트워크 끊김)면 잠깐 쉬고
 * 몇 번 다시 시도한다. GitHub가 순간적으로 느려 504가 떠도 사용자에게 바로 실패로 보이지 않게 한다.
 * 429(요청 과다)·4xx 같은 '다시 해도 같은' 오류는 재시도하지 않고 바로 알린다.
 */
async function fetchLatestRelease(retries = 3): Promise<Response> {
  let lastErr: unknown;
  for (let attempt = 0; attempt < retries; attempt++) {
    if (attempt > 0) await sleep(800 * attempt); // 0 → 0.8s → 1.6s
    try {
      // 15초 안에 응답이 없으면 끊고 재시도(멈춘 요청을 하염없이 기다리지 않게).
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      let res: Response;
      try {
        res = await fetch(LATEST_RELEASE_API, {
          headers: { Accept: 'application/vnd.github+json' },
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
      // 일시적 서버 오류는 재시도, 그 외(성공·4xx 등)는 그대로 반환.
      if (res.status === 502 || res.status === 503 || res.status === 504) {
        lastErr = new Error(`업데이트 확인 실패 (${res.status})`);
        continue;
      }
      return res;
    } catch (e) {
      lastErr = e; // 네트워크 끊김·타임아웃(abort) → 재시도
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error('업데이트 확인 실패 (네트워크)');
}

/** Ask GitHub for the newest release and whether it is newer than this app. */
export async function checkForUpdate(): Promise<UpdateCheck> {
  const res = await fetchLatestRelease();
  if (!res.ok) throw new Error(`업데이트 확인 실패 (${res.status})`);
  const data = await res.json();
  const tag: string = data.tag_name ?? '';
  const build = parseBuild(tag);
  const asset = Array.isArray(data.assets)
    ? data.assets.find((a: { name?: string }) => a.name?.toLowerCase().endsWith('.apk'))
    : null;
  const url: string = asset?.browser_download_url ?? '';
  const latest: UpdateInfo = {
    build,
    tag,
    name: data.name ?? tag,
    url,
    notes: typeof data.body === 'string' ? data.body : '',
  };
  return { available: build > CURRENT_BUILD && !!url, current: CURRENT_BUILD, latest };
}

/**
 * Download the APK (showing progress 0..1) and open Android's installer on it.
 * Android then shows its own "update 도로시?" confirmation — the same signing key means it
 * installs as an update over the existing app, keeping all data.
 */
export async function downloadAndInstall(url: string, onProgress?: (fraction: number) => void): Promise<void> {
  if (Platform.OS !== 'android') throw new Error('안드로이드에서만 앱 설치가 가능해요.');
  if (!url) throw new Error('설치 파일 주소를 찾지 못했어요.');

  const target = `${FileSystem.cacheDirectory}dorothy-update.apk`;
  try {
    await FileSystem.deleteAsync(target, { idempotent: true });
  } catch {
    // a leftover file is fine; downloadResumable overwrites
  }

  const task = FileSystem.createDownloadResumable(url, target, {}, (p) => {
    if (onProgress && p.totalBytesExpectedToWrite > 0) {
      onProgress(p.totalBytesWritten / p.totalBytesExpectedToWrite);
    }
  });
  const result = await task.downloadAsync();
  if (!result?.uri) throw new Error('다운로드에 실패했어요. 와이파이를 확인하고 다시 시도해 주세요.');

  // Android 7+ forbids file:// across apps; hand the installer a content:// URI from Expo's FileProvider.
  const contentUri = await FileSystem.getContentUriAsync(result.uri);
  await IntentLauncher.startActivityAsync('android.intent.action.INSTALL_PACKAGE', {
    data: contentUri,
    flags: 1, // FLAG_GRANT_READ_URI_PERMISSION
  });
}
