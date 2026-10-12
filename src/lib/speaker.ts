import * as FS from 'expo-file-system/legacy';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

/**
 * On-device speaker check for standby mode: learns the owner's voice from a few recordings and, on
 * each wake, decides whether the voice is theirs. Everything runs in JS on the phone (no model
 * download, nothing leaves the device). It uses an MFCC "voiceprint" — good enough to turn away a
 * clearly different voice, but not perfect, so it is opt-in and the threshold adapts to the owner.
 */

const PRINT_KEY = 'dorothy.voicePrint';

export type VoicePrint = { centroid: number[]; threshold: number; samples: number };

let cached: VoicePrint | null | undefined;

export async function loadVoicePrint(): Promise<VoicePrint | null> {
  if (cached !== undefined) return cached;
  if (Platform.OS === 'web') {
    cached = null;
    return cached;
  }
  const raw = await SecureStore.getItemAsync(PRINT_KEY);
  cached = raw ? (JSON.parse(raw) as VoicePrint) : null;
  return cached;
}

export async function hasVoicePrint(): Promise<boolean> {
  return !!(await loadVoicePrint());
}

async function saveVoicePrint(print: VoicePrint | null) {
  cached = print;
  if (Platform.OS === 'web') return;
  if (print) await SecureStore.setItemAsync(PRINT_KEY, JSON.stringify(print));
  else await SecureStore.deleteItemAsync(PRINT_KEY);
}

export async function clearVoicePrint() {
  await saveVoicePrint(null);
}

// ---------- WAV reading ----------

/** Reads a 16-bit PCM mono WAV (what expo-speech-recognition writes on Android) into floats [-1, 1]. */
async function readWavMono(uri: string): Promise<{ samples: Float32Array; sampleRate: number } | null> {
  try {
    const b64 = await FS.readAsStringAsync(uri, { encoding: FS.EncodingType.Base64 });
    const bin = globalThis.atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const view = new DataView(bytes.buffer);
    if (bin.length < 44 || String.fromCharCode(bytes[0], bytes[1], bytes[2], bytes[3]) !== 'RIFF') return null;

    let sampleRate = 16000;
    let channels = 1;
    let bits = 16;
    // Walk the chunks to find "fmt " and "data".
    let offset = 12;
    let dataStart = -1;
    let dataLen = 0;
    while (offset + 8 <= bytes.length) {
      const id = String.fromCharCode(bytes[offset], bytes[offset + 1], bytes[offset + 2], bytes[offset + 3]);
      const size = view.getUint32(offset + 4, true);
      if (id === 'fmt ') {
        channels = view.getUint16(offset + 10, true) || 1;
        sampleRate = view.getUint32(offset + 12, true) || 16000;
        bits = view.getUint16(offset + 22, true) || 16;
      } else if (id === 'data') {
        dataStart = offset + 8;
        dataLen = size;
        break;
      }
      offset += 8 + size + (size & 1);
    }
    if (dataStart < 0 || bits !== 16) return null;

    const bytesPerSample = 2 * channels;
    const count = Math.floor(Math.min(dataLen, bytes.length - dataStart) / bytesPerSample);
    const out = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      // Downmix to mono by taking the first channel.
      out[i] = view.getInt16(dataStart + i * bytesPerSample, true) / 32768;
    }
    return { samples: out, sampleRate };
  } catch {
    return null;
  }
}

// ---------- FFT (iterative radix-2) ----------

function fftPower(frame: Float32Array, size: number): Float32Array {
  const re = new Float32Array(size);
  const im = new Float32Array(size);
  re.set(frame.subarray(0, Math.min(frame.length, size)));
  // Bit-reversal permutation
  for (let i = 1, j = 0; i < size; i++) {
    let bit = size >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let len = 2; len <= size; len <<= 1) {
    const ang = (-2 * Math.PI) / len;
    const wRe = Math.cos(ang);
    const wIm = Math.sin(ang);
    for (let i = 0; i < size; i += len) {
      let curRe = 1;
      let curIm = 0;
      for (let k = 0; k < len / 2; k++) {
        const a = i + k;
        const b = i + k + len / 2;
        const tRe = re[b] * curRe - im[b] * curIm;
        const tIm = re[b] * curIm + im[b] * curRe;
        re[b] = re[a] - tRe;
        im[b] = im[a] - tIm;
        re[a] += tRe;
        im[a] += tIm;
        const nextRe = curRe * wRe - curIm * wIm;
        curIm = curRe * wIm + curIm * wRe;
        curRe = nextRe;
      }
    }
  }
  const half = size / 2 + 1;
  const power = new Float32Array(half);
  for (let i = 0; i < half; i++) power[i] = re[i] * re[i] + im[i] * im[i];
  return power;
}

// ---------- MFCC ----------

const FFT_SIZE = 512;
const NUM_FILTERS = 26;
const NUM_CEPS = 13;

const hzToMel = (hz: number) => 2595 * Math.log10(1 + hz / 700);
const melToHz = (mel: number) => 700 * (10 ** (mel / 2595) - 1);

function melFilterbank(sampleRate: number): Float32Array[] {
  const low = hzToMel(20);
  const high = hzToMel(sampleRate / 2);
  const points = new Array(NUM_FILTERS + 2).fill(0).map((_, i) => melToHz(low + ((high - low) * i) / (NUM_FILTERS + 1)));
  const bins = points.map((hz) => Math.floor(((FFT_SIZE + 1) * hz) / sampleRate));
  const half = FFT_SIZE / 2 + 1;
  return new Array(NUM_FILTERS).fill(0).map((_, m) => {
    const f = new Float32Array(half);
    for (let k = bins[m]; k < bins[m + 1]; k++) if (k >= 0 && k < half) f[k] = (k - bins[m]) / (bins[m + 1] - bins[m] || 1);
    for (let k = bins[m + 1]; k < bins[m + 2]; k++) if (k >= 0 && k < half) f[k] = (bins[m + 2] - k) / (bins[m + 2] - bins[m + 1] || 1);
    return f;
  });
}

// Precomputed DCT-II basis for the cepstral transform.
const DCT = new Array(NUM_CEPS).fill(0).map((_, i) =>
  new Array(NUM_FILTERS).fill(0).map((__, j) => Math.cos((Math.PI * i * (j + 0.5)) / NUM_FILTERS)),
);

/** Turns a recording into a fixed-length voiceprint: the mean and spread of its MFCCs. Returns null if too quiet/short. */
function embedSamples(samples: Float32Array, sampleRate: number): number[] | null {
  // Pre-emphasis.
  const emph = new Float32Array(samples.length);
  for (let i = 1; i < samples.length; i++) emph[i] = samples[i] - 0.97 * samples[i - 1];

  const frameLen = Math.round(0.025 * sampleRate);
  const hop = Math.round(0.01 * sampleRate);
  const window = new Float32Array(frameLen);
  for (let i = 0; i < frameLen; i++) window[i] = 0.54 - 0.46 * Math.cos((2 * Math.PI * i) / (frameLen - 1));
  const filters = melFilterbank(sampleRate);

  const frames: number[][] = [];
  const energies: number[] = [];
  const buf = new Float32Array(FFT_SIZE);
  for (let start = 0; start + frameLen <= emph.length; start += hop) {
    buf.fill(0);
    let energy = 0;
    for (let i = 0; i < frameLen; i++) {
      const s = emph[start + i] * window[i];
      buf[i] = s;
      energy += s * s;
    }
    const power = fftPower(buf, FFT_SIZE);
    const cep = new Array(NUM_CEPS).fill(0);
    const logMel = filters.map((f) => {
      let sum = 0;
      for (let k = 0; k < f.length; k++) sum += f[k] * power[k];
      return Math.log(sum + 1e-8);
    });
    for (let i = 0; i < NUM_CEPS; i++) {
      let v = 0;
      for (let j = 0; j < NUM_FILTERS; j++) v += DCT[i][j] * logMel[j];
      cep[i] = v;
    }
    frames.push(cep);
    energies.push(energy);
  }
  if (frames.length < 10) return null;

  // Keep only frames with speech (energy above a fraction of the loudest frame).
  const maxEnergy = Math.max(...energies);
  const voiced = frames.filter((_, i) => energies[i] > maxEnergy * 0.02);
  const use = voiced.length >= 8 ? voiced : frames;

  const mean = new Array(NUM_CEPS).fill(0);
  for (const f of use) for (let i = 0; i < NUM_CEPS; i++) mean[i] += f[i] / use.length;
  const std = new Array(NUM_CEPS).fill(0);
  for (const f of use) for (let i = 0; i < NUM_CEPS; i++) std[i] += (f[i] - mean[i]) ** 2 / use.length;
  for (let i = 0; i < NUM_CEPS; i++) std[i] = Math.sqrt(std[i]);

  return normalize([...mean, ...std]);
}

function normalize(v: number[]): number[] {
  const norm = Math.sqrt(v.reduce((s, x) => s + x * x, 0)) || 1;
  return v.map((x) => x / norm);
}

function cosine(a: number[], b: number[]): number {
  let dot = 0;
  for (let i = 0; i < a.length; i++) dot += a[i] * b[i];
  return dot; // inputs are unit vectors
}

/** Computes the voiceprint of one recording, or null when the clip is unusable. */
export async function embedRecording(uri: string): Promise<number[] | null> {
  const wav = await readWavMono(uri);
  if (!wav) return null;
  return embedSamples(wav.samples, wav.sampleRate);
}

/**
 * Builds the owner's voiceprint from several recordings and picks a threshold from how much their
 * own samples vary, so the check adapts to the person instead of a fixed guess.
 */
export async function enrollFromRecordings(uris: string[]): Promise<{ ok: boolean; message: string }> {
  const embeds: number[][] = [];
  for (const uri of uris) {
    const e = await embedRecording(uri);
    if (e) embeds.push(e);
  }
  if (embeds.length < 3) return { ok: false, message: '녹음이 잘 안 됐어요. 조용한 곳에서 또박또박 다시 녹음해 주세요.' };

  const centroid = normalize(embeds[0].map((_, i) => embeds.reduce((s, e) => s + e[i], 0) / embeds.length));
  const selfSims = embeds.map((e) => cosine(e, centroid));
  const minSelf = Math.min(...selfSims);
  // A little below the owner's own worst match, clamped to a sane range.
  const threshold = Math.min(0.9, Math.max(0.55, minSelf - 0.08));
  await saveVoicePrint({ centroid, threshold, samples: embeds.length });
  return { ok: true, message: `목소리를 등록했어요 (${embeds.length}개 녹음).` };
}

/** How well a recording matches the owner (0–1), or null if it couldn't be measured. */
export async function matchScore(uri: string): Promise<number | null> {
  const print = await loadVoicePrint();
  if (!print) return null;
  const e = await embedRecording(uri);
  if (!e) return null;
  return cosine(e, print.centroid);
}

/** True if the recording is the owner's voice (passes the learned threshold). Fails open if there is no print. */
export async function isOwner(uri: string | null): Promise<{ owner: boolean; score: number | null }> {
  const print = await loadVoicePrint();
  if (!print) return { owner: true, score: null };
  if (!uri) return { owner: true, score: null }; // no audio to check — don't lock the user out
  const score = await matchScore(uri);
  if (score === null) return { owner: true, score: null };
  return { owner: score >= print.threshold, score };
}

export async function deleteRecording(uri: string | null) {
  if (uri) await FS.deleteAsync(uri, { idempotent: true }).catch(() => {});
}
