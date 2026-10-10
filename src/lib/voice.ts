import * as Speech from 'expo-speech';
import type { ExpoSpeechRecognitionModule as RecognitionModule } from 'expo-speech-recognition';
import { useCallback, useEffect, useRef, useState } from 'react';

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

/**
 * Push-to-talk speech recognition. `onFinal` receives the finished sentence; `transcript`
 * shows what has been heard so far while the user is still talking.
 */
export function useVoiceInput(onFinal: (text: string) => void) {
  const [state, setState] = useState<VoiceState>('idle');
  const [transcript, setTranscript] = useState('');
  const [error, setError] = useState('');
  const onFinalRef = useRef(onFinal);
  useEffect(() => {
    onFinalRef.current = onFinal;
  }, [onFinal]);

  useEffect(() => {
    if (!recognizer) return;
    const subs = [
      recognizer.addListener('start', () => setState('listening')),
      recognizer.addListener('end', () => setState((s) => (s === 'listening' ? 'idle' : s))),
      recognizer.addListener('result', (event) => {
        const text = event.results[0]?.transcript ?? '';
        setTranscript(text);
        if (event.isFinal && text.trim()) {
          setTranscript('');
          onFinalRef.current(text.trim());
        }
      }),
      recognizer.addListener('error', (event) => {
        // Silence or a user cancel is not worth an error message.
        if (event.error === 'no-speech' || event.error === 'aborted') {
          setState('idle');
          return;
        }
        setError(event.error === 'not-allowed' ? '마이크와 음성 인식 권한을 허용해 주세요.' : `음성 인식 오류: ${event.message || event.error}`);
        setState('error');
      }),
    ];
    return () => subs.forEach((s) => s.remove());
  }, []);

  const start = useCallback(async () => {
    if (!recognizer) return;
    stopSpeaking();
    setError('');
    setTranscript('');
    const permission = await recognizer.requestPermissionsAsync();
    if (!permission.granted) {
      setError('마이크와 음성 인식 권한을 허용해 주세요.');
      setState('error');
      return;
    }
    recognizer.start({ lang: LANG, interimResults: true, continuous: false, addsPunctuation: true });
  }, []);

  const stop = useCallback(() => recognizer?.stop(), []);
  const cancel = useCallback(() => {
    recognizer?.abort();
    setTranscript('');
  }, []);

  return { state, transcript, error, start, stop, cancel };
}
