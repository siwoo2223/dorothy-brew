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

// lastText/delivered: some recognizers (seen on Samsung with Google's service) stream partial
// results but finish with an empty or missing final result, so the last partial is sent on 'end'.
function newSession(quiet = false, startedAt = Date.now(), record = false) {
  // started: this hook owns the current recognition. Enrollment (recordUtterance) drives the shared
  // recognizer directly, so the hook ignores those events instead of sending them to the chat.
  return { startedAt, error: undefined as string | undefined, heardSpeech: false, lastText: '', delivered: false, quiet, record, audioUri: null as string | null, started: false };
}

// Fixed path so each standby recording overwrites the last one (no buildup in the cache).
const VOICE_FILE = 'dorothy-standby.wav';

const RECOGNIZER_FAILURES = ['language-not-supported', 'service-not-allowed', 'client', 'busy', 'unknown', 'network'];

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

export function useVoiceInput(
  onFinal: (text: string) => void,
  onEnd?: (info: SessionEnd) => void,
  // When set and a session is started with { record: true }, the finished sentence is delivered
  // only if this returns true — used in standby to accept the owner's voice and ignore others.
  verifyAudio?: (audioUri: string | null, text: string) => Promise<boolean>,
) {
  const [state, setState] = useState<VoiceState>('idle');
  const [transcript, setTranscript] = useState('');
  const [error, setError] = useState('');
  const onFinalRef = useRef(onFinal);
  const onEndRef = useRef(onEnd);
  const verifyAudioRef = useRef(verifyAudio);
  useEffect(() => {
    onFinalRef.current = onFinal;
    onEndRef.current = onEnd;
    verifyAudioRef.current = verifyAudio;
  }, [onFinal, onEnd, verifyAudio]);

  const session = useRef(newSession(false, 0));

  useEffect(() => {
    if (!recognizer) return;
    const subs = [
      recognizer.addListener('start', () => setState('listening')),
      recognizer.addListener('audiostart', (event) => {
        if (event.uri) session.current.audioUri = event.uri;
      }),
      recognizer.addListener('audioend', (event) => {
        if (event.uri) session.current.audioUri = event.uri;
      }),
      recognizer.addListener('end', () => {
        if (!session.current.started) return; // not our session (e.g. enrollment)
        session.current.started = false;
        setState((s) => (s === 'listening' ? 'idle' : s));
        const current = session.current;
        const finish = () => {
          const { startedAt, error, heardSpeech } = current;
          onEndRef.current?.({ error, heardSpeech, durationMs: startedAt ? Date.now() - startedAt : 0 });
        };
        if (!current.delivered && current.lastText) {
          const text = current.lastText;
          const uri = current.audioUri;
          current.delivered = true;
          setTranscript('');
          const verify = verifyAudioRef.current;
          // In record mode the sentence was held back so the voiceprint could be checked here,
          // once the audio file is finalized (the 'audioend' event has fired by now).
          if (current.record && verify) {
            verify(uri, text)
              .then((ok) => {
                if (ok) onFinalRef.current(text);
              })
              .finally(finish);
            return;
          }
          onFinalRef.current(text);
        } else if (!current.heardSpeech && !current.error && !current.quiet) {
          setError('잘 못 들었어요. 🎙️을 누르고 다시 말해 주세요.');
        }
        finish();
      }),
      recognizer.addListener('result', (event) => {
        if (!session.current.started) return; // not our session (e.g. enrollment)
        const text = event.results[0]?.transcript ?? '';
        if (text.trim()) {
          session.current.heardSpeech = true;
          session.current.lastText = text.trim();
          setError('');
        }
        setTranscript(text);
        if (event.isFinal && text.trim() && !session.current.delivered) {
          if (session.current.record) {
            // Hold the sentence; it is verified against the voiceprint and delivered on 'end',
            // once the recorded audio file is ready.
            session.current.lastText = text.trim();
          } else {
            session.current.delivered = true;
            setTranscript('');
            onFinalRef.current(text.trim());
            // Continuous sessions keep going after a final result; start collecting the next sentence.
            session.current.delivered = false;
            session.current.lastText = '';
          }
        }
      }),
      recognizer.addListener('error', (event) => {
        if (!session.current.started) return; // not our session (e.g. enrollment)
        session.current.error = event.error;
        // If the preferred Google service misbehaves on this phone, use the phone's default next time.
        if (servicePackage && !session.current.heardSpeech && RECOGNIZER_FAILURES.includes(event.error)) {
          servicePackage = '';
        }
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

  /** `continuous` keeps listening across pauses (Android 13+; also silences the start beep). `record` saves the audio for the voiceprint check. */
  const start = useCallback(async (opts: { continuous?: boolean; quiet?: boolean; record?: boolean } = {}) => {
    if (!recognizer) return;
    stopSpeaking();
    // In standby a failed restart is handled by the caller; don't flash an error for every retry.
    if (!opts.quiet) setError('');
    setTranscript('');
    session.current = newSession(!!opts.quiet, Date.now(), !!opts.record);
    session.current.started = true;
    const permission = await recognizer.requestPermissionsAsync();
    if (!permission.granted) {
      session.current.started = false;
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
      // Overwrites the same file each time, so recordings don't pile up in the cache.
      recordingOptions: opts.record ? { persist: true, outputFileName: VOICE_FILE } : undefined,
    });
  }, []);

  const showError = useCallback((message: string) => {
    setError(message);
    setState('error');
  }, []);

  const stop = useCallback(() => recognizer?.stop(), []);
  const cancel = useCallback(() => {
    // A cancelled session must not send whatever was half-heard.
    session.current.delivered = true;
    recognizer?.abort();
    setTranscript('');
  }, []);

  return { state, transcript, error, start, stop, cancel, showError };
}

/**
 * Records one spoken phrase for voiceprint enrollment and resolves its saved audio file URI (or
 * null if nothing usable was captured). Drives the recognizer directly with its own listeners;
 * useVoiceInput ignores these events because it didn't start the session.
 */
export function recordUtterance(fileName: string, maxMs = 6000): Promise<string | null> {
  return new Promise((resolve) => {
    if (!recognizer) return resolve(null);
    let uri: string | null = null;
    let heard = false;
    let done = false;
    const subs: { remove: () => void }[] = [];
    const cleanup = () => subs.forEach((s) => s.remove());
    const finishWith = (value: string | null) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      cleanup();
      resolve(value);
    };
    subs.push(
      recognizer.addListener('audiostart', (e) => e.uri && (uri = e.uri)),
      recognizer.addListener('audioend', (e) => e.uri && (uri = e.uri)),
      recognizer.addListener('result', (e) => {
        if (e.results[0]?.transcript?.trim()) heard = true;
      }),
      recognizer.addListener('end', () => finishWith(heard ? uri : null)),
      recognizer.addListener('error', (e) => finishWith(e.error === 'no-speech' ? null : heard ? uri : null)),
    );
    const timer = setTimeout(() => recognizer?.stop(), maxMs);
    recognizer
      .requestPermissionsAsync()
      .then((p) => {
        if (!p.granted) return finishWith(null);
        recognizer!.start({ lang: LANG, interimResults: true, continuous: false, androidRecognitionServicePackage: androidService(), recordingOptions: { persist: true, outputFileName: fileName } });
      })
      .catch(() => finishWith(null));
  });
}

/**
 * Transcribes a saved audio file (e.g. a call recording — the recognizer decodes m4a/amr to PCM
 * internally). Returns the combined text, best-effort. Runs entirely on the recognizer; the chat
 * hook ignores these events because it didn't start the session.
 */
export type TranscribeOptions = { sampleRate?: number; channels?: number; onDevice?: boolean; timeoutMs?: number };

function transcribeOnce(uri: string, opts: TranscribeOptions): Promise<string> {
  return new Promise((resolve) => {
    if (!recognizer) return resolve('');
    const parts: string[] = [];
    let done = false;
    const subs: { remove: () => void }[] = [];
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      subs.forEach((s) => s.remove());
      resolve(parts.join(' ').replace(/\s+/g, ' ').trim());
    };
    subs.push(
      recognizer.addListener('result', (e) => {
        const text = e.results[0]?.transcript?.trim();
        if (e.isFinal && text) parts.push(text);
      }),
      recognizer.addListener('end', finish),
      recognizer.addListener('error', () => finish()),
    );
    const timer = setTimeout(() => recognizer?.stop(), opts.timeoutMs ?? 240000);
    recognizer
      .requestPermissionsAsync()
      .then((p) => {
        if (!p.granted) return finish();
        recognizer!.start({
          lang: LANG,
          interimResults: false,
          continuous: true,
          // On-device avoids the "Server disconnected" error when streaming a file to the online service.
          requiresOnDeviceRecognition: opts.onDevice,
          // The file is decoded but not resampled, so the recognizer must be told the file's real rate.
          audioSource: { uri, sampleRate: opts.sampleRate, audioChannels: opts.channels },
        });
      })
      .catch(() => finish());
  });
}

/**
 * Transcribes a saved audio file. Tries on-device recognition first (reliable for streamed files),
 * then falls back to the online service. Needs the file's real sample rate (see audioInfo).
 */
export async function transcribeAudioFile(uri: string, opts: TranscribeOptions = {}): Promise<string> {
  const onDevice = await transcribeOnce(uri, { ...opts, onDevice: true });
  if (onDevice) return onDevice;
  return transcribeOnce(uri, { ...opts, onDevice: false });
}
