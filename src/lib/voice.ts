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

// Which system voice reads replies aloud; set from the saved setting (see setSpeechVoice).
let speechVoice: string | undefined;
export function setSpeechVoice(identifier?: string) {
  speechVoice = identifier || undefined;
}

// How fast Dorothy speaks (1.0 = normal). Set from the saved setting.
let speechRate = 1.05;
export function setSpeechRate(rate?: number) {
  if (typeof rate === 'number' && rate > 0) speechRate = rate;
}

export function speak(text: string, onDone?: () => void) {
  Speech.stop();
  Speech.speak(forSpeech(text), {
    language: LANG,
    rate: speechRate,
    voice: speechVoice,
    onDone,
    onError: () => onDone?.(),
  });
}

export function stopSpeaking() {
  Speech.stop();
}

/** Korean text-to-speech voices available on this phone, for letting the user pick Dorothy's voice. */
export async function listKoreanVoices(): Promise<{ identifier: string; name: string }[]> {
  try {
    const voices = await Speech.getAvailableVoicesAsync();
    return voices
      .filter((v) => v.language?.toLowerCase().startsWith('ko'))
      .map((v) => ({ identifier: v.identifier, name: v.name || v.identifier }));
  } catch {
    return [];
  }
}

/** Speaks a short sample with a given voice, so the user can hear it before choosing. */
export function previewVoice(identifier?: string) {
  Speech.stop();
  Speech.speak('안녕하세요, 저는 도로시예요. 이 목소리로 말할게요.', { language: LANG, rate: speechRate, voice: identifier || undefined });
}

type VoiceState = 'idle' | 'listening' | 'error';

// lastText/delivered: some recognizers (seen on Samsung with Google's service) stream partial
// results but finish with an empty or missing final result, so the last partial is sent on 'end'.
function newSession(quiet = false, startedAt = Date.now(), record = false, pushToTalk = false) {
  // started: this hook owns the current recognition. Enrollment (recordUtterance) drives the shared
  // recognizer directly, so the hook ignores those events instead of sending them to the chat.
  // pushToTalk/holding/finals: in 꾹-눌러-말하기, nothing is sent until the finger is lifted; the
  // recognizer auto-finalizing on a pause just appends to `finals` (and we keep listening while held).
  return { startedAt, error: undefined as string | undefined, heardSpeech: false, lastText: '', delivered: false, quiet, record, pushToTalk, holding: false, finals: [] as string[], audioUri: null as string | null, started: false };
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
  onFinal: (text: string, audioUri?: string | null) => void,
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
  // The exact options last passed to recognizer.start(), so 꾹-눌러-말하기 can restart after a pause.
  const startOptsRef = useRef<Parameters<NonNullable<typeof recognizer>['start']>[0] | null>(null);

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
        const current = session.current;
        if (!current.started) return; // not our session (e.g. enrollment)
        // 꾹 눌러 말하기: 손가락이 아직 눌려 있는데 인식기가 (잠깐의 침묵으로) 멈춘 것뿐이면,
        // 전송하지 말고 계속 듣는다. 이미 모은 말(finals)은 그대로 둔다.
        if (current.pushToTalk && current.holding && !current.error && startOptsRef.current) {
          const opts = startOptsRef.current;
          setTimeout(() => {
            if (session.current === current && current.holding && current.started) {
              try { recognizer!.start(opts); } catch { /* ignore busy */ }
            }
          }, 120);
          return;
        }
        current.started = false;
        setState((s) => (s === 'listening' ? 'idle' : s));
        const finish = () => {
          const { startedAt, error, heardSpeech } = current;
          onEndRef.current?.({ error, heardSpeech, durationMs: startedAt ? Date.now() - startedAt : 0 });
        };
        // 꾹 눌러 말하기: 손을 뗀 지금, 모은 말 전체 + 녹음 파일을 한 번만 전달(서버 받아쓰기용 uri 포함).
        if (current.pushToTalk) {
          const text = (current.finals.join(' ').trim() || current.lastText.trim());
          const uri = current.audioUri;
          current.delivered = true;
          setTranscript('');
          if (text || uri) onFinalRef.current(text, uri);
          else if (!current.quiet) setError('잘 못 들었어요. 입력칸을 꾹 누르고 다시 말해 주세요.');
          finish();
          return;
        }
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
        const cur = session.current;
        const text = event.results[0]?.transcript ?? '';
        const trimmed = text.trim();
        if (trimmed) { cur.heardSpeech = true; setError(''); }
        // 꾹 눌러 말하기: 전송하지 않고, 지금까지 모은 말 + 현재 말을 화면에만 보여준다.
        if (cur.pushToTalk) {
          const live = [cur.finals.join(' '), trimmed].filter(Boolean).join(' ').trim();
          setTranscript(live);
          cur.lastText = live;
          if (event.isFinal && trimmed) { cur.finals.push(trimmed); cur.lastText = cur.finals.join(' '); }
          return;
        }
        if (trimmed) cur.lastText = trimmed;
        setTranscript(text);
        if (event.isFinal && trimmed && !cur.delivered) {
          if (cur.record) {
            // Hold the sentence; it is verified against the voiceprint and delivered on 'end',
            // once the recorded audio file is ready.
            cur.lastText = trimmed;
          } else {
            cur.delivered = true;
            setTranscript('');
            onFinalRef.current(trimmed);
            // Continuous sessions keep going after a final result; start collecting the next sentence.
            cur.delivered = false;
            cur.lastText = '';
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
        const permission = event.error === 'not-allowed' || event.error === 'service-not-allowed';
        // In standby (quiet) the loop silently restarts on every ending, so a transient recognizer
        // glitch (busy/client/network/server) must NOT flash a red error — only a hard permission
        // problem is worth surfacing. A phone that keeps failing is still caught by the standby
        // failure counter, which turns standby off with a clear message after a few tries.
        if (session.current.quiet && !permission) {
          setState('idle');
          return;
        }
        setError(permission ? '마이크와 음성 인식 권한을 허용해 주세요.' : `음성 인식 오류: ${event.message || event.error}`);
        setState('error');
      }),
    ];
    return () => subs.forEach((s) => s.remove());
  }, []);

  /** `continuous` keeps listening across pauses (Android 13+; also silences the start beep). `record` saves the audio for the voiceprint check. `pushToTalk` holds everything until the finger lifts. */
  const start = useCallback(async (opts: { continuous?: boolean; quiet?: boolean; record?: boolean; pushToTalk?: boolean } = {}) => {
    if (!recognizer) return;
    stopSpeaking();
    // In standby a failed restart is handled by the caller; don't flash an error for every retry.
    if (!opts.quiet) setError('');
    setTranscript('');
    const record = !!opts.record || !!opts.pushToTalk; // 꾹 눌러 말하기는 서버 받아쓰기용으로 항상 녹음한다.
    session.current = newSession(!!opts.quiet, Date.now(), record, !!opts.pushToTalk);
    session.current.started = true;
    if (opts.pushToTalk) session.current.holding = true;
    const permission = await recognizer.requestPermissionsAsync();
    if (!permission.granted) {
      session.current.started = false;
      setError('마이크와 음성 인식 권한을 허용해 주세요.');
      setState('error');
      return;
    }
    const sro = {
      lang: LANG,
      interimResults: true,
      // 꾹 눌러 말하기는 손을 뗄 때까지 계속 들어야 하므로 항상 연속 모드.
      continuous: !!opts.continuous || !!opts.pushToTalk,
      addsPunctuation: true,
      androidRecognitionServicePackage: androidService(),
      // Wait longer through pauses so Dorothy doesn't cut in while the user is still speaking.
      androidIntentOptions: {
        EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS: 2000,
        EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS: 2500,
        EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS: 2000,
      },
      // Overwrites the same file each time, so recordings don't pile up in the cache.
      recordingOptions: record ? { persist: true, outputFileName: VOICE_FILE } : undefined,
    };
    startOptsRef.current = sro;
    recognizer.start(sro);
  }, []);

  const showError = useCallback((message: string) => {
    setError(message);
    setState('error');
  }, []);

  /** 꾹 눌러 말하기 시작(누르는 동안 녹음·대기, 전송 안 함). */
  const pushStart = useCallback(() => start({ pushToTalk: true }), [start]);
  /** 손을 뗐을 때: 더 듣지 말고 멈춰서 'end'에서 모은 말+녹음을 한 번 전달. */
  const pushEnd = useCallback(() => {
    if (session.current.pushToTalk && session.current.started) {
      session.current.holding = false;
      recognizer?.stop();
    }
  }, []);

  const stop = useCallback(() => recognizer?.stop(), []);
  const cancel = useCallback(() => {
    // A cancelled session must not send whatever was half-heard.
    session.current.delivered = true;
    recognizer?.abort();
    setTranscript('');
  }, []);

  return { state, transcript, error, start, stop, cancel, showError, pushStart, pushEnd };
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
