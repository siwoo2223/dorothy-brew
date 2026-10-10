import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, FlatList, KeyboardAvoidingView, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Input } from '../components/ui';
import { chat, describeError } from '../lib/assistant';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import { extractWakeCommand, speak, stopSpeaking, useVoiceInput, voiceInputAvailable } from '../lib/voice';

const SUGGESTIONS = ['내일 아침 8시에 운동 알려줘', '나에 대해 뭘 알고 있어?', '오늘 마닐라 날씨 어때?', '이번 주에 놓친 거 있어?'];

type Phase = 'off' | 'wake' | 'command' | 'busy';

export default function Chat() {
  const t = useTheme();
  // Tab screens render a standard navigation header above the content.
  const headerHeight = useSafeAreaInsets().top + 44;
  const { state, actions, getState } = useStore();
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const list = useRef<FlatList>(null);
  const router = useRouter();
  const params = useLocalSearchParams<{ voice?: string }>();
  const canListen = voiceInputAvailable();

  // Standby ("대기 모드"): listen for "도로시야", answer aloud, take one follow-up, then wait again.
  // phase: wake = waiting for the name, command = listening for a request, busy = thinking/speaking.
  const [standby, setStandbyState] = useState(false);
  const phase = useRef<Phase>('off');
  const listeningFor = useRef<'wake' | 'command' | null>(null);
  // Recognizers that can't keep a session open end (or error) right away. Count those in a row so
  // standby backs off, drops continuous mode, and finally gives up instead of beeping on and off.
  const failures = useRef(0);
  const continuousOk = useRef(Platform.OS !== 'android' || Number(Platform.Version) >= 33);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // send is declared below and needs `voice`, so the recognizer reaches it through a ref.
  const sendRef = useRef<(text: string, spoken?: boolean) => void>(() => {});

  const voice = useVoiceInput(
    (text) => {
      if (phase.current === 'wake') {
        const command = extractWakeCommand(text);
        if (command === null) return; // not for Dorothy; keep waiting
        voice.cancel();
        if (command === '') {
          phase.current = 'command';
          speak(`네${getState().profile.name ? `, ${getState().profile.name}` : ''}?`, listenForCommand);
          return;
        }
        text = command;
      }
      if (phase.current !== 'off') phase.current = 'busy';
      sendRef.current(text, true);
    },
    (info) => {
      // The recognizer stops on silence or timeouts; in standby, quietly pick up listening again.
      const was = listeningFor.current;
      listeningFor.current = null;
      if (was === 'command' && phase.current === 'command') phase.current = 'wake';
      if (phase.current !== 'wake' || (was !== 'wake' && was !== 'command')) return;

      const silence = info.error === undefined || info.error === 'no-speech' || info.error === 'speech-timeout';
      const healthy = info.heardSpeech || (silence && info.durationMs > 3000);
      failures.current = healthy ? 0 : failures.current + 1;

      if (failures.current >= 3 && continuousOk.current) {
        // This phone's recognizer doesn't keep continuous sessions open; use one-shot sessions instead.
        continuousOk.current = false;
        failures.current = 0;
      } else if (failures.current >= 5) {
        setStandby(false);
        voice.showError(
          info.error === 'not-allowed' || info.error === 'service-not-allowed'
            ? '마이크와 음성 인식 권한을 허용해 주세요.'
            : `이 휴대폰의 음성 인식이 계속 끊겨서 대기 모드를 껐어요${info.error ? ` (${info.error})` : ''}. Google 앱을 설치·업데이트하면 나아질 수 있어요. 🎙️ 버튼은 그대로 쓸 수 있어요.`,
        );
        return;
      }
      // Give the recognizer time to release the mic (avoids "busy"), longer after each failure.
      const delay = Math.min(800 * 2 ** failures.current, 8000);
      retryTimer.current = setTimeout(listenForWake, delay);
    },
  );
  const listening = voice.state === 'listening';

  function listenForWake() {
    if (phase.current !== 'wake') return;
    listeningFor.current = 'wake';
    // Continuous recognition (no beep, no gaps) needs Android 13+ and a recognizer that supports it.
    voice.start({ continuous: continuousOk.current, quiet: true });
  }

  function listenForCommand() {
    if (phase.current !== 'command') return;
    listeningFor.current = 'command';
    voice.start();
  }

  const setStandby = (on: boolean) => {
    setStandbyState(on);
    if (retryTimer.current) clearTimeout(retryTimer.current);
    retryTimer.current = null;
    if (on) {
      activateKeepAwakeAsync('standby');
      phase.current = 'wake';
      failures.current = 0;
      listenForWake();
    } else {
      deactivateKeepAwake('standby');
      phase.current = 'off';
      listeningFor.current = null;
      stopSpeaking();
      voice.cancel();
    }
  };

  // Leaving the chat tab stops listening and speaking.
  useFocusEffect(
    useCallback(() => {
      return () => setStandby(false);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []),
  );

  // Opened with ?voice=1 (home mic button, "listen on open") or ?voice=standby: start right away.
  // Kept separate from the blur cleanup above so clearing the param doesn't switch standby back off.
  useEffect(() => {
    if (!params.voice || !canListen) return;
    const requested = params.voice;
    router.setParams({ voice: undefined });
    if (requested === 'standby') {
      if (phase.current === 'off') setStandby(true);
    } else if (phase.current === 'off' && voice.state !== 'listening') {
      voice.start();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.voice]);

  const afterReply = () => {
    if (phase.current === 'off') return;
    // Allow one follow-up without the name; silence falls back to waiting for "도로시야".
    phase.current = 'command';
    listenForCommand();
  };

  const send = async (text: string, spoken = false) => {
    const message = text.trim();
    if (!message || busy) return;
    if (!state.apiKey) {
      Alert.alert('API 키 필요', '설정 탭에서 Claude API 키를 먼저 입력해 주세요.');
      return;
    }
    setDraft('');
    setBusy(true);
    // Snapshot before appending so the new message is not sent twice.
    const snapshot = getState();
    actions.appendChat({ role: 'user', text: message });
    const voiceReply = spoken || phase.current !== 'off';
    try {
      const reply = await chat(message, snapshot, actions, { spoken: voiceReply });
      actions.appendChat({ role: 'assistant', text: reply });
      if (voiceReply) speak(reply, afterReply);
    } catch (e) {
      const msg = describeError(e);
      actions.appendChat({ role: 'assistant', text: `⚠️ ${msg}` });
      if (voiceReply) speak(msg, afterReply);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    sendRef.current = send;
  });

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined} keyboardVerticalOffset={headerHeight}>
      <FlatList
        ref={list}
        data={state.chat}
        keyExtractor={(m) => m.id}
        contentContainerStyle={styles.list}
        onContentSizeChange={() => list.current?.scrollToEnd({ animated: true })}
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={[styles.emptyTitle, { color: t.text }]}>무엇을 도와드릴까요?</Text>
            <Text style={{ color: t.subtext, textAlign: 'center' }}>나에 대해 이야기해 주시면 기억해 두고, 할 일은 알림으로 챙겨 드려요.</Text>
            {SUGGESTIONS.map((s) => (
              <Pressable key={s} onPress={() => send(s)} style={[styles.chip, { borderColor: t.border, backgroundColor: t.card }]}>
                <Text style={{ color: t.text }}>{s}</Text>
              </Pressable>
            ))}
          </View>
        }
        renderItem={({ item }) => {
          const mine = item.role === 'user';
          return (
            <View
              style={[
                styles.bubble,
                mine
                  ? { alignSelf: 'flex-end', backgroundColor: t.userBubble }
                  : { alignSelf: 'flex-start', backgroundColor: t.assistantBubble, borderColor: t.border, borderWidth: StyleSheet.hairlineWidth },
              ]}
            >
              <Text selectable style={{ color: mine ? '#fff' : t.text, fontSize: 15, lineHeight: 22 }}>
                {item.text}
              </Text>
            </View>
          );
        }}
        ListFooterComponent={busy ? <ActivityIndicator style={{ alignSelf: 'flex-start', margin: 8 }} /> : null}
      />
      {(voice.error || canListen) && (
        <View style={[styles.voiceBar, { borderTopColor: t.border }]}>
          {voice.error ? (
            <Text style={{ color: t.danger, fontSize: 12, flex: 1 }}>{voice.error}</Text>
          ) : (
            <Text style={{ color: standby ? t.accent : t.subtext, fontSize: 12, flex: 1 }}>
              {standby
                ? busy
                  ? '💭 생각하고 있어요…'
                  : listeningFor.current === 'command'
                    ? '🎙️ 말씀하세요'
                    : '👂 대기 중 — "도로시야"라고 불러 주세요'
                : listening
                  ? '🎙️ 듣고 있어요… 말을 마치면 자동으로 보내요'
                  : '🎙️ 버튼을 누르거나 대기 모드를 켜 보세요'}
            </Text>
          )}
          {canListen && (
            <Pressable
              onPress={() => setStandby(!standby)}
              style={[styles.modePill, { borderColor: standby ? t.accent : t.border, backgroundColor: standby ? t.accent : 'transparent' }]}
            >
              <Text style={{ color: standby ? t.accentText : t.text, fontSize: 12 }}>👂 대기 모드</Text>
            </Pressable>
          )}
        </View>
      )}
      <View style={[styles.composer, { borderTopColor: t.border, backgroundColor: t.bg }]}>
        {canListen && (
          <Pressable
            onPress={() => (listening ? voice.stop() : voice.start())}
            disabled={busy || standby}
            accessibilityLabel={listening ? '말하기 끝내기' : '음성으로 말하기'}
            style={[styles.mic, { backgroundColor: listening ? t.danger : t.card, borderColor: listening ? t.danger : t.border, opacity: busy || standby ? 0.4 : 1 }]}
          >
            <Text style={{ fontSize: 18 }}>{listening ? '⏹' : '🎙️'}</Text>
          </Pressable>
        )}
        <Input
          style={{ flex: 1, maxHeight: 120 }}
          value={listening ? voice.transcript : draft}
          onChangeText={setDraft}
          placeholder={listening ? '듣고 있어요…' : '도로시에게 말하기…'}
          editable={!listening}
          multiline
          onSubmitEditing={() => send(draft)}
        />
        <Pressable onPress={() => send(draft)} disabled={busy || !draft.trim()} style={[styles.send, { backgroundColor: t.accent, opacity: busy || !draft.trim() ? 0.4 : 1 }]}>
          <Text style={{ color: '#fff', fontWeight: '700' }}>전송</Text>
        </Pressable>
      </View>
      {state.chat.length > 0 && (
        <Pressable
          onPress={() => Alert.alert('대화 지우기', '대화 기록만 지워요. 기억과 할 일은 그대로 남아요.', [{ text: '취소' }, { text: '지우기', style: 'destructive', onPress: actions.clearChat }])}
          style={styles.clear}
        >
          <Text style={{ color: t.subtext, fontSize: 12 }}>대화 지우기</Text>
        </Pressable>
      )}
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  list: { padding: 16, gap: 10, flexGrow: 1 },
  empty: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 10, paddingTop: 40 },
  emptyTitle: { fontSize: 20, fontWeight: '700' },
  chip: { borderWidth: 1, borderRadius: 20, paddingHorizontal: 14, paddingVertical: 8 },
  bubble: { maxWidth: '85%', borderRadius: 18, paddingHorizontal: 14, paddingVertical: 10 },
  composer: { flexDirection: 'row', gap: 8, padding: 10, borderTopWidth: StyleSheet.hairlineWidth, alignItems: 'flex-end' },
  send: { borderRadius: 12, paddingHorizontal: 16, paddingVertical: 13 },
  mic: { borderRadius: 12, borderWidth: 1, width: 46, height: 46, alignItems: 'center', justifyContent: 'center' },
  voiceBar: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 12, paddingTop: 8, borderTopWidth: StyleSheet.hairlineWidth },
  modePill: { borderWidth: 1, borderRadius: 14, paddingHorizontal: 10, paddingVertical: 5 },
  clear: { position: 'absolute', top: 6, right: 12 },
});
