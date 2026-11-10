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

/** Ask GitHub for the newest release and whether it is newer than this app. */
export async function checkForUpdate(): Promise<UpdateCheck> {
  const res = await fetch(LATEST_RELEASE_API, {
    headers: { Accept: 'application/vnd.github+json' },
  });
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
