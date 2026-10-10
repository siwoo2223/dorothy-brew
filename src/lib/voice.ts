import * as Speech from 'expo-speech';
import type { ExpoSpeechRecognitionModule as RecognitionModule } from 'expo-speech-recognition';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Platform } from 'react-native';

const LANG = 'ko-KR';

// The recognizer is a native module that Expo Go doesn't ship. Load it lazily so the rest of the
// app still runs there; voice input simply reports itself unavailable.
let recognizer: typeof RecognitionModule | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  recognizer = require('expo-speech-recognition').ExpoSpeechRecognitionModule;
} catch {
  recognizer = null;
}

// Samsung phones often default to a recognizer that can't listen continuously and stops at once.
// Prefer Google's recognizer when it is installed.
const PREFERRED_SERVICES = ['com.google.android.googlequicksearchbox'];
let servicePackage: string | undefined;
function androidService(): string | undefined {
  if (servicePackage !== undefined || Platform.OS !== 'android' || !recognizer) return servicePackage || undefined;
  try {
    const available = recognizer.getSpeechRecognitionServices();
    servicePackage = PREFERRED_SERVICES.find((p) => available.includes(p)) ?? '';
  } catch {
    servicePackage = '';
  }
  return servicePackage || undefined;
}

export function voiceInputAvailable(): boolean {
  try {
    return !!recognizer?.isRecognitionAvailable();
  } catch {
    return false;
  }
}

let EMOJI: RegExp | null = null;
try {
  EMOJI = new RegExp('\\p{Extended_Pictographic}', 'gu');
} catch {
  EMOJI = null;
}

/** Turns a chat reply into something pleasant to hear: no markdown, links, or emoji. */
function forSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/https?:\/\/\S+/g, '')
    .replace(/[*_#>`~|]/g, '')
    .replace(/^\s*[-•]\s+/gm, '')
    .replace(EMOJI ?? /(?!)/g, '')
    .replace(/\s+\n/g, '\n')
    .trim();
}

export function speak(text: string, onDone?: () => void) {
  Speech.stop();
  Speech.speak(forSpeech(text), {
    language: LANG,
    rate: 1.05,
    onDone,
    onError: () => onDone?.(),
  });
}

export function stopSpeaking() {
  Speech.stop();
}

type VoiceState = 'idle' | 'listening' | 'error';

// Recognizers spell the name several ways ("도로시야", "도로 시", "돌아시").
const WAKE_WORD = /(도\s?로\s?시|돌\s?로\s?시|돌아시|도로씨)(야|아|여)?[\s,.!?~]*/;

/**
 * Returns the request that follows the wake word ("도로시야 내일 일정 알려줘" → "내일 일정 알려줘"),
 * an empty string when only the name was said, or null when the wake word wasn't heard.
 */
export function extractWakeCommand(text: string): string | null {
  const match = WAKE_WORD.exec(text);
  if (!match) return null;
  return text.slice(match.index + match[0].length).trim();
}

/**
 * Push-to-talk speech recognition. `onFinal` receives the finished sentence; `transcript`
 * shows what has been heard so far while the user is still talking.
 */
/** How a listening session ended, so callers can tell a normal pause from a recognizer that keeps failing. */
export type SessionEnd = { error?: string; durationMs: number; heardSpeech: boolean };

export function useVoiceInput(onFinal: (text: string) => void, onEnd?: (info: SessionEnd) => void) {
  const [state, setState] = useState<VoiceState>('idle');
  const [transcript, setTranscript] = useState('');
  const [error, setError] = useState('');
  const onFinalRef = useRef(onFinal);
  const onEndRef = useRef(onEnd);
  useEffect(() => {
    onFinalRef.current = onFinal;
    onEndRef.current = onEnd;
  }, [onFinal, onEnd]);

  const session = useRef({ startedAt: 0, error: undefined as string | undefined, heardSpeech: false });

  useEffect(() => {
    if (!recognizer) return;
    const subs = [
      recognizer.addListener('start', () => setState('listening')),
      recognizer.addListener('end', () => {
        setState((s) => (s === 'listening' ? 'idle' : s));
        const { startedAt, error, heardSpeech } = session.current;
        onEndRef.current?.({ error, heardSpeech, durationMs: startedAt ? Date.now() - startedAt : 0 });
      }),
      recognizer.addListener('result', (event) => {
        const text = event.results[0]?.transcript ?? '';
        if (text.trim()) {
          session.current.heardSpeech = true;
          setError('');
        }
        setTranscript(text);
        if (event.isFinal && text.trim()) {
          setTranscript('');
          onFinalRef.current(text.trim());
        }
      }),
      recognizer.addListener('error', (event) => {
        session.current.error = event.error;
        // Silence or a user cancel is not worth an error message.
        if (event.error === 'no-speech' || event.error === 'aborted' || event.error === 'speech-timeout') {
          setState('idle');
          return;
        }
        setError(event.error === 'not-allowed' ? '마이크와 음성 인식 권한을 허용해 주세요.' : `음성 인식 오류: ${event.message || event.error}`);
        setState('error');
      }),
    ];
    return () => subs.forEach((s) => s.remove());
  }, []);

  /** `continuous` keeps listening across pauses (Android 13+; also silences the start beep). */
  const start = useCallback(async (opts: { continuous?: boolean; quiet?: boolean } = {}) => {
    if (!recognizer) return;
    stopSpeaking();
    // In standby a failed restart is handled by the caller; don't flash an error for every retry.
    if (!opts.quiet) setError('');
    setTranscript('');
    session.current = { startedAt: Date.now(), error: undefined, heardSpeech: false };
    const permission = await recognizer.requestPermissionsAsync();
    if (!permission.granted) {
      setError('마이크와 음성 인식 권한을 허용해 주세요.');
      setState('error');
      return;
    }
    recognizer.start({
      lang: LANG,
      interimResults: true,
      continuous: !!opts.continuous,
      addsPunctuation: true,
      androidRecognitionServicePackage: androidService(),
    });
  }, []);

  const showError = useCallback((message: string) => {
    setError(message);
    setState('error');
  }, []);

  const stop = useCallback(() => recognizer?.stop(), []);
  const cancel = useCallback(() => {
    recognizer?.abort();
    setTranscript('');
  }, []);

  return { state, transcript, error, start, stop, cancel, showError };
}
