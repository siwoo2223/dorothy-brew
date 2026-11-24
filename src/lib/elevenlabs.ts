// ElevenLabs 고급 목소리(선택): 켜 두면 도로시가 폰 기본 TTS 대신 사람 같은 성우 목소리로 말한다.
// 키는 이 기기에만(SecureStore) 저장하고, 말할 때마다 짧게 ElevenLabs로 음성을 만들어 재생한다.
// 실패하면 조용히 폰 기본 목소리로 되돌아간다(speak() 참고).
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Buffer } from 'buffer';
import * as FS from 'expo-file-system/legacy';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

const KEY = 'dorothy.elevenKey';
const VOICE_KEY = 'dorothy.elevenVoiceId';
const MODEL_KEY = 'dorothy.elevenModel';

// 한국어를 지원하는 다국어 모델(품질 우선). 더 빠른 걸 원하면 설정에서 바꿀 수 있게 열어 둔다.
const DEFAULT_MODEL = 'eleven_multilingual_v2';
// 기본 목소리: 차분한 영국식 남성(집사 느낌). 설정에서 voiceId로 바꿀 수 있다.
const DEFAULT_VOICE = 'onwK4e9ZLuTAKqWW03F9';

// 참고용 추천 목소리(JARVIS 느낌). 설정 화면에서 골라 넣을 수 있게.
export const ELEVEN_VOICES: { id: string; name: string; note: string }[] = [
  { id: 'onwK4e9ZLuTAKqWW03F9', name: 'Daniel', note: '영국 남성 · 차분/권위(집사 느낌)' },
  { id: 'JBFqnCBsd6RMkjVDRZzb', name: 'George', note: '영국 남성 · 따뜻함' },
  { id: 'N2lVS1w4EtoT3dr4eOWO', name: 'Callum', note: '남성 · 강렬' },
  { id: '21m00Tcm4TlvDq8ikWAM', name: 'Rachel', note: '여성 · 차분' },
  { id: 'XB0fDUnXU5powFXDhCwa', name: 'Charlotte', note: '여성 · 부드러움' },
];

// 동기 체크(speak에서 바로 쓰려고)용 캐시. 앱 시작 때 loadEleven()으로 채운다.
let cachedKey = '';
let cachedVoice = DEFAULT_VOICE;
let cachedModel = DEFAULT_MODEL;

async function readSecure(key: string): Promise<string> {
  try {
    const v = Platform.OS === 'web' ? await AsyncStorage.getItem(key) : await SecureStore.getItemAsync(key);
    return v ?? '';
  } catch {
    return '';
  }
}

/** 앱 시작 시 호출 — 저장된 키/목소리를 캐시에 올려 둔다. */
export async function loadEleven(): Promise<void> {
  cachedKey = await readSecure(KEY);
  cachedVoice = (await AsyncStorage.getItem(VOICE_KEY)) || DEFAULT_VOICE;
  cachedModel = (await AsyncStorage.getItem(MODEL_KEY)) || DEFAULT_MODEL;
}

export async function setElevenKey(value: string): Promise<void> {
  const v = value.trim();
  cachedKey = v;
  if (Platform.OS === 'web') {
    if (v) await AsyncStorage.setItem(KEY, v); else await AsyncStorage.removeItem(KEY);
  } else {
    if (v) await SecureStore.setItemAsync(KEY, v); else await SecureStore.deleteItemAsync(KEY);
  }
}

export async function setElevenVoice(voiceId: string): Promise<void> {
  const v = voiceId.trim() || DEFAULT_VOICE;
  cachedVoice = v;
  await AsyncStorage.setItem(VOICE_KEY, v);
}

export function elevenConfigured(): boolean {
  return !!cachedKey;
}
export function elevenVoiceId(): string {
  return cachedVoice || DEFAULT_VOICE;
}
export async function elevenKeyHint(): Promise<string> {
  const k = await readSecure(KEY);
  return k ? k.slice(-4) : '';
}

/**
 * text → ElevenLabs로 음성(mp3)을 만들어 파일로 저장하고 그 경로를 돌려준다. 실패하면 throw.
 * 재생은 voice.ts(speak)가 expo-audio로 한다.
 */
export async function elevenTtsToFile(text: string): Promise<string> {
  const key = cachedKey;
  if (!key) throw new Error('ElevenLabs 키가 없어요.');
  const voice = cachedVoice || DEFAULT_VOICE;
  const url = `https://api.elevenlabs.io/v1/text-to-speech/${voice}?output_format=mp3_44100_128`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'xi-api-key': key, 'Content-Type': 'application/json', Accept: 'audio/mpeg' },
    body: JSON.stringify({
      text: text.slice(0, 2000), // 아주 긴 답변은 잘라 비용·지연을 줄인다
      model_id: cachedModel || DEFAULT_MODEL,
      voice_settings: { stability: 0.45, similarity_boost: 0.8, style: 0.0, use_speaker_boost: true },
    }),
  });
  if (!res.ok) {
    const t = await res.text().catch(() => '');
    throw new Error(`ElevenLabs ${res.status}: ${t.slice(0, 120)}`);
  }
  const ab = await res.arrayBuffer();
  const base64 = Buffer.from(new Uint8Array(ab)).toString('base64');
  const uri = `${FS.cacheDirectory}dorothy-tts-${Date.now() % 100000}.mp3`;
  await FS.writeAsStringAsync(uri, base64, { encoding: FS.EncodingType.Base64 });
  return uri;
}
