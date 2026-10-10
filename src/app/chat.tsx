import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, FlatList, KeyboardAvoidingView, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Input } from '../components/ui';
import { chat, describeError } from '../lib/assistant';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import { speak, stopSpeaking, useVoiceInput, voiceInputAvailable } from '../lib/voice';

const SUGGESTIONS = ['내일 아침 8시에 운동 알려줘', '나에 대해 뭘 알고 있어?', '오늘 마닐라 날씨 어때?', '이번 주에 놓친 거 있어?'];

export default function Chat() {
  const t = useTheme();
  // Tab screens render a standard navigation header above the content.
  const headerHeight = useSafeAreaInsets().top + 44;
  const { state, actions, getState } = useStore();
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  // Hands-free mode: Dorothy reads every reply aloud and starts listening again when she finishes.
  const [voiceMode, setVoiceModeState] = useState(false);
  const voiceModeRef = useRef(false);
  const setVoiceMode = (on: boolean) => {
    voiceModeRef.current = on;
    setVoiceModeState(on);
  };
  const list = useRef<FlatList>(null);
  const router = useRouter();
  const params = useLocalSearchParams<{ voice?: string }>();
  const canListen = voiceInputAvailable();

  // send is declared below and needs `voice`, so the recognizer reaches it through a ref.
  const sendRef = useRef<(text: string, spoken?: boolean) => void>(() => {});
  const voice = useVoiceInput((text) => sendRef.current(text, true));
  const listening = voice.state === 'listening';

  // Opened from the home screen's mic button: start listening right away, once.
  useFocusEffect(
    useCallback(() => {
      if (params.voice === '1' && canListen) {
        router.setParams({ voice: undefined });
        voice.start();
      }
      return () => {
        stopSpeaking();
        voice.cancel();
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [params.voice]),
  );

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
    try {
      const reply = await chat(message, snapshot, actions, { spoken: spoken || voiceModeRef.current });
      actions.appendChat({ role: 'assistant', text: reply });
      if (spoken || voiceModeRef.current) {
        speak(reply, () => {
          if (voiceModeRef.current) voice.start();
        });
      }
    } catch (e) {
      actions.appendChat({ role: 'assistant', text: `⚠️ ${describeError(e)}` });
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
            <Text style={{ color: t.subtext, fontSize: 12, flex: 1 }}>
              {listening ? '🎙️ 듣고 있어요… 말을 마치면 자동으로 보내요' : voiceMode ? '음성 대화 중 — 도로시가 답을 읽어 주고 다시 들어요' : '🎙️ 버튼을 누르고 말해 보세요'}
            </Text>
          )}
          {canListen && (
            <Pressable
              onPress={() => {
                const next = !voiceMode;
                setVoiceMode(next);
                if (next && !listening && !busy) voice.start();
                if (!next) {
                  stopSpeaking();
                  voice.cancel();
                }
              }}
              style={[styles.modePill, { borderColor: voiceMode ? t.accent : t.border, backgroundColor: voiceMode ? t.accent : 'transparent' }]}
            >
              <Text style={{ color: voiceMode ? t.accentText : t.text, fontSize: 12 }}>🔊 음성 대화</Text>
            </Pressable>
          )}
        </View>
      )}
      <View style={[styles.composer, { borderTopColor: t.border, backgroundColor: t.bg }]}>
        {canListen && (
          <Pressable
            onPress={listening ? voice.stop : voice.start}
            disabled={busy}
            accessibilityLabel={listening ? '말하기 끝내기' : '음성으로 말하기'}
            style={[styles.mic, { backgroundColor: listening ? t.danger : t.card, borderColor: listening ? t.danger : t.border, opacity: busy ? 0.4 : 1 }]}
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
