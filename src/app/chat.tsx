import { useRef, useState } from 'react';
import { ActivityIndicator, Alert, FlatList, KeyboardAvoidingView, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Input } from '../components/ui';
import { chat, describeError } from '../lib/assistant';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';

const SUGGESTIONS = ['내일 아침 8시에 운동 알려줘', '나에 대해 뭘 알고 있어?', '오늘 서울 날씨 어때?', '이번 주에 놓친 거 있어?'];

export default function Chat() {
  const t = useTheme();
  // Tab screens render a standard navigation header above the content.
  const headerHeight = useSafeAreaInsets().top + 44;
  const { state, actions, getState } = useStore();
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const list = useRef<FlatList>(null);

  const send = async (text: string) => {
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
      const reply = await chat(message, snapshot, actions);
      actions.appendChat({ role: 'assistant', text: reply });
    } catch (e) {
      actions.appendChat({ role: 'assistant', text: `⚠️ ${describeError(e)}` });
    } finally {
      setBusy(false);
    }
  };

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
      <View style={[styles.composer, { borderTopColor: t.border, backgroundColor: t.bg }]}>
        <Input
          style={{ flex: 1, maxHeight: 120 }}
          value={draft}
          onChangeText={setDraft}
          placeholder="도로시에게 말하기…"
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
  clear: { position: 'absolute', top: 6, right: 12 },
});
