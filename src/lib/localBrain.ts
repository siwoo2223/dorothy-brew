import * as FS from 'expo-file-system/legacy';
import type { LlamaContext } from 'llama.rn';
import { Platform } from 'react-native';

/**
 * Dorothy's on-device brain: an open-weights model run with llama.cpp (llama.rn). Free, private
 * (nothing leaves the phone), and works offline, at the cost of being less capable than Claude.
 *
 * Q4_0 because llama.cpp's Android GPU backend (OpenCL on Adreno) accelerates only Q4_0 / Q6_K.
 */
export const LOCAL_MODEL = {
  name: 'Qwen2.5 3B Instruct',
  file: 'qwen2.5-3b-instruct-q4_0.gguf',
  url: 'https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_0.gguf',
  approxMB: 2000,
};

// Native module; absent on web and in Expo Go, so load it lazily like the speech recognizer.
let llama: typeof import('llama.rn') | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  llama = require('llama.rn');
} catch {
  llama = null;
}

const DIR = `${FS.documentDirectory ?? ''}models/`;
const PATH = DIR + LOCAL_MODEL.file;
const PART = PATH + '.part';

export function localBrainSupported(): boolean {
  return Platform.OS !== 'web' && !!llama && !!FS.documentDirectory;
}

export async function localModelDownloaded(): Promise<boolean> {
  if (!localBrainSupported()) return false;
  const info = await FS.getInfoAsync(PATH);
  return info.exists && (info.size ?? 0) > 100 * 1024 * 1024;
}

let download: FS.DownloadResumable | null = null;

/** Downloads the model file (about 2 GB). Reports progress from 0 to 1. */
export async function downloadLocalModel(onProgress: (fraction: number) => void): Promise<void> {
  if (!localBrainSupported()) throw new Error('이 기기에서는 휴대폰 두뇌를 쓸 수 없어요.');
  const free = await FS.getFreeDiskStorageAsync();
  if (free < (LOCAL_MODEL.approxMB + 500) * 1024 * 1024) {
    throw new Error(`저장 공간이 부족해요. 최소 ${Math.ceil((LOCAL_MODEL.approxMB + 500) / 1024)}GB가 필요해요.`);
  }
  await FS.makeDirectoryAsync(DIR, { intermediates: true }).catch(() => {});
  await FS.deleteAsync(PART, { idempotent: true });
  download = FS.createDownloadResumable(LOCAL_MODEL.url, PART, {}, (p) => {
    if (p.totalBytesExpectedToWrite > 0) onProgress(p.totalBytesWritten / p.totalBytesExpectedToWrite);
  });
  try {
    const result = await download.downloadAsync();
    if (!result) return; // cancelled
    if (result.status !== 200) throw new Error(`모델 다운로드 실패 (HTTP ${result.status})`);
    await FS.moveAsync({ from: PART, to: PATH });
  } finally {
    download = null;
  }
}

export async function cancelLocalDownload(): Promise<void> {
  await download?.cancelAsync().catch(() => {});
  download = null;
  await FS.deleteAsync(PART, { idempotent: true });
}

export async function deleteLocalModel(): Promise<void> {
  await releaseLocalBrain();
  await FS.deleteAsync(PATH, { idempotent: true });
}

let context: LlamaContext | null = null;
let loading: Promise<LlamaContext> | null = null;

async function getContext(): Promise<LlamaContext> {
  if (context) return context;
  if (loading) return loading;
  if (!llama) throw new Error('이 기기에서는 휴대폰 두뇌를 쓸 수 없어요.');
  const lib = llama;
  loading = (async () => {
    const base = { model: PATH, n_ctx: 4096, use_mlock: false };
    try {
      // Offload to the GPU when the phone supports it (Adreno on recent Galaxy phones).
      return await lib.initLlama({ ...base, n_gpu_layers: 99 });
    } catch {
      return await lib.initLlama({ ...base, n_gpu_layers: 0 });
    }
  })();
  try {
    context = await loading;
    return context;
  } finally {
    loading = null;
  }
}

export async function releaseLocalBrain(): Promise<void> {
  const ctx = context;
  context = null;
  await ctx?.release().catch(() => {});
}

export type LocalMessage = { role: 'system' | 'user' | 'assistant'; content: string };

const STOP = ['<|im_end|>', '<|endoftext|>', '</s>', '<|eot_id|>'];

/** Generates a reply on the phone, streaming tokens to `onToken` as they arrive. */
export async function localComplete(messages: LocalMessage[], onToken?: (token: string) => void, maxTokens = 600): Promise<string> {
  if (!(await localModelDownloaded())) {
    throw new Error('휴대폰 두뇌 모델이 아직 없어요. 설정 → 도로시의 두뇌에서 먼저 내려받아 주세요.');
  }
  const ctx = await getContext();
  const result = await ctx.completion({ messages, n_predict: maxTokens, temperature: 0.6, stop: STOP }, (data) => onToken?.(data.token));
  return result.text.trim();
}
