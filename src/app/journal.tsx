import { useMemo, useState } from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Input, Title } from '../components/ui';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';

/**
 * 여행·생활 일지: 날짜별로 어디서 뭐했는지 남긴다. 도로시가 "작년 오늘쯤" 자동으로 떠올려 준다
 * (아침 알림 + 대화에서). 여기서 직접 추가·삭제할 수 있고, 모두 이 폰에만 저장된다.
 */
export default function Journal() {
  const t = useTheme();
  const { state, actions } = useStore();
  const [title, setTitle] = useState('');
  const [place, setPlace] = useState('');
  const [note, setNote] = useState('');
  const today = new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(today);

  const add = () => {
    if (!title.trim()) return;
    actions.addJournalEntry({ date: date.trim() || today, title: title.trim(), place: place.trim() || undefined, note: note.trim() || undefined });
    setTitle(''); setPlace(''); setNote(''); setDate(today);
  };

  const entries = useMemo(() => [...state.journal].sort((a, b) => b.date.localeCompare(a.date)), [state.journal]);

  // "작년 오늘쯤"(올해 제외, 같은 월·일 ±4일)
  const onThisDay = useMemo(() => {
    const now = new Date();
    const md = (d: Date) => d.getMonth() * 100 + d.getDate();
    const todayMd = md(now);
    return entries.filter((j) => {
      const d = new Date(j.date + 'T00:00:00');
      return !isNaN(d.getTime()) && d.getFullYear() < now.getFullYear() && Math.abs(md(d) - todayMd) <= 4;
    });
  }, [entries]);

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Body muted>
        어디서 뭐했는지 날짜별로 남겨두면, 도로시가 &quot;작년 이맘때&quot;에 알아서 떠올려 드려요. 도로시에게 &quot;오늘 ○○ 다녀왔어, 일지에 적어줘&quot;라고 말해도 돼요. 모두 이 폰에만 저장돼요.
      </Body>

      {onThisDay.length > 0 && (
        <Card style={{ borderColor: t.accent, borderWidth: 1 }}>
          <Title>🕰️ 작년 이맘때</Title>
          {onThisDay.map((j) => (
            <Text key={j.id} style={{ color: t.text, fontSize: 15, lineHeight: 22 }}>
              {j.date.slice(0, 4)}년 · {j.title}{j.place ? ` (${j.place})` : ''}
            </Text>
          ))}
        </Card>
      )}

      <Card>
        <Title>➕ 일지 쓰기</Title>
        <Input value={date} onChangeText={setDate} placeholder="날짜 (YYYY-MM-DD)" autoCapitalize="none" />
        <Input value={title} onChangeText={setTitle} placeholder="무엇을 했나요 (예: 타가이타이 일출)" />
        <Input value={place} onChangeText={setPlace} placeholder="장소 (예: 타가이타이 Picnic Grove)" />
        <Input value={note} onChangeText={setNote} placeholder="메모·느낌 (선택)" multiline />
        <Button label="일지에 저장" onPress={add} disabled={!title.trim()} />
      </Card>

      {entries.length === 0 && <Card><Body muted>아직 일지가 없어요. 첫 기록을 남겨보세요.</Body></Card>}

      {entries.map((j) => (
        <Card key={j.id}>
          <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 10 }}>
            <View style={{ flex: 1 }}>
              <Text style={{ color: t.subtext, fontSize: 12, fontWeight: '700' }}>{j.date}{j.place ? ` · ${j.place}` : ''}</Text>
              <Text style={{ color: t.text, fontSize: 16, fontWeight: '700', marginTop: 2 }}>{j.title}</Text>
              {j.note ? <Text style={{ color: t.text, fontSize: 14, lineHeight: 21, marginTop: 4 }}>{j.note}</Text> : null}
            </View>
            <Pressable
              hitSlop={8}
              onPress={() => Alert.alert('일지 지우기', `${j.date} · ${j.title}`, [{ text: '취소' }, { text: '지우기', style: 'destructive', onPress: () => actions.deleteJournalEntry(j.id) }])}
            >
              <Text style={{ color: t.subtext, fontSize: 20 }}>×</Text>
            </Pressable>
          </View>
        </Card>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
});
