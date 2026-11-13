import { CameraView, useCameraPermissions, type CameraType } from 'expo-camera';
import * as Haptics from 'expo-haptics';
import { router } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Markdown } from '../components/Markdown';
import { chat, describeError } from '../lib/assistant';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import { speak, stopSpeaking, useVoiceInput, voiceInputAvailable } from '../lib/voice';

const tap = () => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});

/**
 * "카메라 모드": point the camera and talk. On a spoken question (or the shutter) Dorothy snaps the
 * current frame, sends it to Claude with the question, and answers aloud — a practical stand-in for a
 * live video assistant (the API can't stream video, but a frame-per-question feels the same). In
 * continuous mode it listens again after each answer so it's hands-free.
 */
export default function CameraScreen() {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const { getState, actions } = useStore();
  const [perm, requestPerm] = useCameraPermissions();
  const cam = useRef<CameraView>(null);
  const [facing, setFacing] = useState<CameraType>('back');
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState('');
  const [continuous, setContinuous] = useState(false);
  const continuousRef = useRef(false);
  const canListen = voiceInputAvailable();

  const askRef = useRef<(q: string) => void>(() => {});
  const voiceRef = useRef<ReturnType<typeof useVoiceInput> | null>(null);

  const voice = useVoiceInput(
    (text) => {
      const q = text.trim();
      if (q) askRef.current(q);
    },
    () => {
      // When a listening session ends with no question and we're in continuous mode, pick up again.
      if (continuousRef.current && !busy) setTimeout(() => voiceRef.current?.start({ quiet: true }), 400);
    },
  );
  const listening = voice.state === 'listening';

  const ask = useCallback(async (question: string) => {
    if (busy) return;
    const cameraReady = cam.current;
    if (!cameraReady) return;
    stopSpeaking();
    voice.cancel();
    setBusy(true);
    setAnswer('');
    try {
      const shot = await cameraReady.takePictureAsync({ base64: true, quality: 0.45, skipProcessing: true });
      if (!shot?.base64) throw new Error('사진을 찍지 못했어요. 다시 시도해 주세요.');
      if (getState().model === 'local') {
        setAnswer('카메라 모드는 Claude 두뇌가 필요해요. 설정 → 도로시 두뇌에서 Claude로 바꿔 주세요.');
        return;
      }
      let streamed = '';
      const reply = await chat(question, getState(), actions, {
        spoken: true,
        image: { base64: shot.base64, mediaType: 'image/jpeg' },
        onToken: (tok) => { streamed += tok; setAnswer(streamed); },
      });
      setAnswer(reply);
      // Speak the answer; when it finishes, resume listening in continuous mode.
      speak(reply, () => {
        if (continuousRef.current) voice.start({ quiet: true });
      });
    } catch (e) {
      setAnswer(`⚠️ ${describeError(e)}`);
    } finally {
      setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy]);

  useEffect(() => {
    askRef.current = ask;
    voiceRef.current = voice;
  });

  const toggleContinuous = () => {
    tap();
    const next = !continuous;
    setContinuous(next);
    continuousRef.current = next;
    if (next && !busy) voice.start({ quiet: true });
    else { voice.cancel(); stopSpeaking(); }
  };

  const leave = () => { continuousRef.current = false; voice.cancel(); stopSpeaking(); router.back(); };

  if (!perm) return <View style={[styles.fill, { backgroundColor: '#000' }]} />;
  if (!perm.granted) {
    return (
      <View style={[styles.center, { backgroundColor: t.bg, paddingTop: insets.top }]}>
        <Text style={{ color: t.text, fontSize: 16, textAlign: 'center', marginBottom: 16 }}>
          카메라로 보여주며 대화하려면{'\n'}카메라 권한이 필요해요.
        </Text>
        <Pressable onPress={() => { tap(); requestPerm(); }} style={[styles.pill, { backgroundColor: t.accent }]}>
          <Text style={{ color: t.accentText, fontWeight: '700' }}>카메라 권한 허용</Text>
        </Pressable>
        <Pressable onPress={leave} style={{ marginTop: 14 }}>
          <Text style={{ color: t.subtext }}>뒤로</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.fill}>
      <CameraView ref={cam} style={styles.fill} facing={facing} />

      {/* Top bar */}
      <View style={[styles.topBar, { paddingTop: insets.top + 6 }]}>
        <Pressable onPress={leave} hitSlop={10} style={styles.iconBtn}>
          <Text style={styles.iconTxt}>✕</Text>
        </Pressable>
        <Text style={styles.topTitle}>📸 카메라 모드</Text>
        <Pressable onPress={() => { tap(); setFacing((f) => (f === 'back' ? 'front' : 'back')); }} hitSlop={10} style={styles.iconBtn}>
          <Text style={styles.iconTxt}>🔄</Text>
        </Pressable>
      </View>

      {/* Answer / status overlay */}
      <View style={[styles.answerWrap, { bottom: insets.bottom + 120 }]} pointerEvents="box-none">
        {listening && !busy ? (
          <View style={styles.badge}><Text style={styles.badgeTxt}>🎙️ 듣고 있어요{voice.transcript ? ` — ${voice.transcript}` : '… 보여주며 물어보세요'}</Text></View>
        ) : null}
        {(busy || answer) ? (
          <View style={styles.answerCard}>
            {busy && !answer ? (
              <View style={styles.row}><ActivityIndicator color="#fff" /><Text style={styles.answerTxt}>  보고 있어요…</Text></View>
            ) : (
              <ScrollView style={{ maxHeight: 220 }}>
                <Markdown text={answer} color="#fff" />
              </ScrollView>
            )}
          </View>
        ) : null}
      </View>

      {/* Bottom controls */}
      <View style={[styles.controls, { paddingBottom: insets.bottom + 18 }]}>
        <Pressable onPress={toggleContinuous} style={[styles.sideBtn, { borderColor: continuous ? t.accent : 'rgba(255,255,255,0.5)', backgroundColor: continuous ? t.accent : 'rgba(0,0,0,0.35)' }]}>
          <Text style={{ color: '#fff', fontSize: 12, fontWeight: '700' }}>{continuous ? '연속 ON' : '연속 대화'}</Text>
        </Pressable>

        {/* Shutter: describe what's in view */}
        <Pressable
          onPress={() => { tap(); ask('지금 카메라에 보이는 걸 자세히 설명해 줘. 글자가 있으면 읽어 주고, 중요한 건 정리해 줘.'); }}
          disabled={busy}
          style={[styles.shutter, { opacity: busy ? 0.5 : 1 }]}
        >
          <Text style={{ fontSize: 26 }}>📷</Text>
        </Pressable>

        {/* Mic: ask by voice */}
        {canListen ? (
          <Pressable
            onPress={() => { tap(); if (listening) voice.stop(); else voice.start({ quiet: true }); }}
            disabled={busy}
            style={[styles.sideBtn, { borderColor: listening ? '#E5698C' : 'rgba(255,255,255,0.5)', backgroundColor: listening ? '#E5698C' : 'rgba(0,0,0,0.35)', opacity: busy ? 0.5 : 1 }]}
          >
            <Text style={{ color: '#fff', fontSize: 18 }}>{listening ? '⏹' : '🎙️'}</Text>
          </Pressable>
        ) : (
          <View style={styles.sideBtn} />
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  pill: { paddingHorizontal: 20, paddingVertical: 12, borderRadius: 24 },
  topBar: { position: 'absolute', top: 0, left: 0, right: 0, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingBottom: 8 },
  topTitle: { color: '#fff', fontWeight: '800', fontSize: 15, textShadowColor: 'rgba(0,0,0,0.6)', textShadowRadius: 4 },
  iconBtn: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center', backgroundColor: 'rgba(0,0,0,0.35)' },
  iconTxt: { color: '#fff', fontSize: 18 },
  answerWrap: { position: 'absolute', left: 14, right: 14, gap: 8 },
  badge: { alignSelf: 'flex-start', backgroundColor: 'rgba(0,0,0,0.55)', paddingHorizontal: 12, paddingVertical: 7, borderRadius: 16 },
  badgeTxt: { color: '#fff', fontSize: 13 },
  answerCard: { backgroundColor: 'rgba(0,0,0,0.6)', borderRadius: 16, padding: 14 },
  answerTxt: { color: '#fff', fontSize: 15 },
  row: { flexDirection: 'row', alignItems: 'center' },
  controls: { position: 'absolute', left: 0, right: 0, bottom: 0, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-around', paddingHorizontal: 24 },
  shutter: { width: 74, height: 74, borderRadius: 37, backgroundColor: 'rgba(255,255,255,0.92)', alignItems: 'center', justifyContent: 'center', borderWidth: 4, borderColor: 'rgba(255,255,255,0.6)' },
  sideBtn: { minWidth: 64, height: 48, paddingHorizontal: 12, borderRadius: 24, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center' },
});
