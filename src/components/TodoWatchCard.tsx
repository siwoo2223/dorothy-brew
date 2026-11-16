import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Input, Title } from './ui';
import { inboxSupported, notificationAccessGranted } from '../lib/inbox';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';

const PRESETS = ['한아름마트', '기나', '기나글로벌'];

/**
 * "카톡 할 일 감지": watch only the rooms the owner lists. When someone in one of these rooms asks
 * the owner to do something, Dorothy proposes it as a to-do (the owner approves/ignores in 할 일).
 * Message text isn't stored — only the task sentence. Android only (needs notification access).
 */
export function TodoWatchCard() {
  const t = useTheme();
  const { state, actions } = useStore();
  const rooms = state.todoRooms ?? [];
  const [input, setInput] = useState('');

  if (!inboxSupported()) return null;

  const add = (r: string) => { const v = r.trim(); if (v && !rooms.includes(v)) actions.setTodoRooms([...rooms, v]); setInput(''); };
  const remove = (r: string) => actions.setTodoRooms(rooms.filter((x) => x !== r));

  return (
    <Card>
      <Title>📥 카톡 할 일 감지</Title>
      <Body muted>
        여기 적은 방(카톡 등)에서 누가 사장님께 &quot;보내 주세요/확인 부탁/회신 주세요&quot; 하면, 도로시가 할 일 후보로 올려요. 바로 등록하지 않고 할 일 탭에서 먼저 물어봐요. 메시지 원문은 저장하지 않아요.
      </Body>
      {!notificationAccessGranted() && <Body muted>먼저 위 &quot;메시지 알림 접근&quot;을 켜 주세요. 그래야 방 메시지를 읽을 수 있어요.</Body>}

      <View style={styles.wrap}>
        {rooms.length === 0 ? (
          <Body muted>아직 감지할 방이 없어요. 아래에서 방 이름을 더해 보세요.</Body>
        ) : (
          rooms.map((r) => (
            <Pressable key={r} onPress={() => remove(r)} style={[styles.chip, { backgroundColor: t.accentSoft, borderColor: t.accent }]}>
              <Text style={{ color: t.accent, fontWeight: '700' }}>{r}</Text>
              <Text style={{ color: t.accent, fontWeight: '700' }}> ✕</Text>
            </Pressable>
          ))
        )}
      </View>

      <Input value={input} onChangeText={setInput} placeholder="방 이름 입력 후 추가" autoCapitalize="none" onSubmitEditing={() => add(input)} />
      <Button label="방 추가" variant="ghost" onPress={() => add(input)} disabled={!input.trim()} />

      <Body muted>자주 쓰는 방:</Body>
      <View style={styles.wrap}>
        {PRESETS.filter((p) => !rooms.includes(p)).map((p) => (
          <Pressable key={p} onPress={() => add(p)} style={[styles.chip, { backgroundColor: t.card, borderColor: t.border }]}>
            <Text style={{ color: t.text }}>+ {p}</Text>
          </Pressable>
        ))}
      </View>
      <Body muted>방 이름이 조금 바뀌어도 부분 일치로 찾아요. 방을 빼면 그 방의 감지 기록도 지워져요.</Body>
    </Card>
  );
}

const styles = StyleSheet.create({
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 7 },
});
