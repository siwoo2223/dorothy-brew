// Optional Gemini integration: turns a call recording's audio into a Korean summary (+ action items)
// in one call, so Dorothy can "remember" what a call was about. The key lives only on this device
// (SecureStore, like the Claude key) and the audio is sent to Google's Gemini API when the owner
// has turned this on. Free tier is generous; the model is cheap.
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

const KEY = 'dorothy.geminiApiKey';
const MODEL = 'gemini-2.0-flash';
const ENDPOINT = (model: string, key: string) =>
  `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${encodeURIComponent(key)}`;

async function getKey(): Promise<string> {
  try {
    const v = Platform.OS === 'web' ? await AsyncStorage.getItem(KEY) : await SecureStore.getItemAsync(KEY);
    return v ?? '';
  } catch {
    return '';
  }
}

export async function setGeminiKey(value: string): Promise<void> {
  const v = value.trim();
  if (Platform.OS === 'web') {
    if (v) await AsyncStorage.setItem(KEY, v); else await AsyncStorage.removeItem(KEY);
  } else {
    if (v) await SecureStore.setItemAsync(KEY, v); else await SecureStore.deleteItemAsync(KEY);
  }
}

export async function geminiConfigured(): Promise<boolean> {
  return !!(await getKey());
}

/** Last 4 chars for a "saved (…abcd)" hint, or '' when not set. */
export async function geminiKeyHint(): Promise<string> {
  const k = await getKey();
  return k ? k.slice(-4) : '';
}

const PROMPT =
  '이 통화 녹음을 듣고 한국어로 정리해 줘. 형식:\n' +
  '1) 한 줄 요약\n' +
  '2) 핵심 내용 (불릿 2~5개)\n' +
  '3) 내가 해야 할 일·회신 (있으면 불릿, 없으면 생략)\n' +
  '녹음에 없는 내용은 지어내지 말고, 잘 안 들리는 부분은 "불명확"이라고 해. 사람 이름/숫자는 들린 그대로.';

/** Sends the recording audio (base64) to Gemini and returns a Korean summary. Throws on failure. */
export async function summarizeCallAudio(base64: string, mimeType: string): Promise<string> {
  const key = await getKey();
  if (!key) throw new Error('Gemini 키가 없어요. 설정에서 넣어 주세요.');
  let res: Response;
  try {
    res = await fetch(ENDPOINT(MODEL, key), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        contents: [{ parts: [{ inline_data: { mime_type: mimeType, data: base64 } }, { text: PROMPT }] }],
        generationConfig: { temperature: 0.2 },
      }),
    });
  } catch {
    throw new Error('Gemini 서버에 연결할 수 없어요. 인터넷을 확인해 주세요.');
  }
  const text = await res.text();
  if (res.status === 400) throw new Error('Gemini가 이 녹음을 처리하지 못했어요(형식/키 확인).');
  if (res.status === 401 || res.status === 403) throw new Error('Gemini 키가 올바르지 않아요. 설정에서 다시 넣어 주세요.');
  if (res.status === 429) throw new Error('Gemini 무료 한도를 잠시 초과했어요. 잠시 뒤 다시 시도해 주세요.');
  if (!res.ok) throw new Error(`Gemini 오류 ${res.status}: ${text.slice(0, 160)}`);
  let json: { candidates?: { content?: { parts?: { text?: string }[] } }[] };
  try { json = JSON.parse(text); } catch { throw new Error('Gemini 응답을 읽지 못했어요.'); }
  const out = (json.candidates?.[0]?.content?.parts ?? []).map((p) => p.text ?? '').filter(Boolean).join('\n').trim();
  if (!out) throw new Error('Gemini가 빈 요약을 돌려줬어요.');
  return out;
}
