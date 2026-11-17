import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Input, Title } from './ui';
import { inboxSupported, notificationAccessGranted } from '../lib/inbox';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';

// 사장님이 쓰는 환전방(부분 일치로 찾음). 방 이름이 길어 핵심 단어만 넣는다.
const PRESETS = ['손대손', '환전/호텔방'];

/**
 * "단톡방 평균 환율": 여기 적은 방(환전방)에서만 올라온 환율 숫자를 모아 자금·손익 화면에서
 * 방별 평균 + 합친 평균으로 보여준다. 메시지 원문은 저장하지 않는다. Android만(알림 접근 필요).
 */
export function FxRoomCard() {
  const t = useTheme();
  const { state, actions } = useStore();
  const rooms = state.fxRooms ?? [];
  const [input, setInput] = useState('');

  if (!inboxSupported()) return null;

  const add = (r: string) => { const v = r.trim(); if (v && !rooms.includes(v)) actions.setFxRooms([...rooms, v]); setInput(''); };
  const remove = (r: string) => actions.setFxRooms(rooms.filter((x) => x !== r));

  return (
    <Card>
      <Title>💱 단톡방 환율 방</Title>
      <Body muted>
        여기 적은 환전방에서 올라온 환율만 모아, 자금·손익 화면에서 방별 평균과 합친 평균을 보여줘요. 숫자(페소 환율)만 쓰고 메시지 원문은 저장하지 않아요.
      </Body>
      {!notificationAccessGranted() && <Body muted>먼저 위 &quot;메시지 알림 접근&quot;을 켜 주세요. 그래야 방 메시지를 읽을 수 있어요.</Body>}

      <View style={styles.wrap}>
        {rooms.length === 0 ? (
          <Body muted>아직 환율 방이 없어요. 아래에서 방 이름을 더해 보세요.</Body>
        ) : (
          rooms.map((r) => (
            <Pressable key={r} onPress={() => remove(r)} style={[styles.chip, { backgroundColor: t.accentSoft, borderColor: t.accent }]}>
              <Text style={{ color: t.accent, fontWeight: '700' }}>{r}</Text>
              <Text style={{ color: t.accent, fontWeight: '700' }}> ✕</Text>
            </Pressable>
          ))
        )}
      </View>

      <Input value={input} onChangeText={setInput} placeholder="방 이름(일부) 입력 후 추가" autoCapitalize="none" onSubmitEditing={() => add(input)} />
      <Button label="방 추가" variant="ghost" onPress={() => add(input)} disabled={!input.trim()} />

      <Body muted>자주 쓰는 방:</Body>
      <View style={styles.wrap}>
        {PRESETS.filter((p) => !rooms.includes(p)).map((p) => (
          <Pressable key={p} onPress={() => add(p)} style={[styles.chip, { backgroundColor: t.card, borderColor: t.border }]}>
            <Text style={{ color: t.text }}>+ {p}</Text>
          </Pressable>
        ))}
      </View>
      <Body muted>방 이름 전체가 아니라 핵심 단어(예: &apos;손대손&apos;, &apos;환전/호텔방&apos;)만 넣어도 부분 일치로 찾아요.</Body>
    </Card>
  );
}

const styles = StyleSheet.create({
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 7 },
});
