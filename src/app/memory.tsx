import { useState } from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Input, Title } from '../components/ui';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';

export default function MemoryScreen() {
  const t = useTheme();
  const { state, actions } = useStore();
  const [category, setCategory] = useState('');
  const [content, setContent] = useState('');

  const add = () => {
    if (!content.trim()) return;
    actions.addMemory(category.trim() || '기타', content.trim());
    setContent('');
    setCategory('');
  };

  const groups = new Map<string, typeof state.memories>();
  for (const m of state.memories) groups.set(m.category, [...(groups.get(m.category) ?? []), m]);

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Body muted>
        도로시가 알고 있는 사장님에 대한 사실이에요. 대화하면서 자동으로 쌓이고, 여기서 직접 추가·삭제할 수 있어요. 모두 이 휴대폰에만 저장돼요.
      </Body>

      <Card>
        <Title>➕ 기억 추가</Title>
        <Input value={category} onChangeText={setCategory} placeholder="분류 (예: 가족, 건강, 취향)" />
        <Input value={content} onChangeText={setContent} placeholder="기억할 내용 (예: 커피는 디카페인만 마심)" multiline />
        <Button label="기억하기" onPress={add} disabled={!content.trim()} />
      </Card>

      {state.memories.length > 0 && (
        <Text style={{ color: t.subtext, fontSize: 13, fontWeight: '700', paddingHorizontal: 4 }}>
          기억 {state.memories.length}개 · {groups.size}개 분류
        </Text>
      )}

      {state.memories.length === 0 && (
        <Card>
          <Body muted>아직 쌓인 기억이 없어요. 도로시에게 사장님에 대해 이야기해 보세요. 설정의 ‘🧠 나 자동 파악’을 켜두면 스스로도 조금씩 알아가요.</Body>
        </Card>
      )}

      {[...groups.entries()].map(([cat, items]) => (
        <Card key={cat}>
          <View style={styles.catHeader}>
            <View style={[styles.dot, { backgroundColor: t.accent }]} />
            <Title>{cat}</Title>
            <Text style={{ color: t.subtext, fontSize: 12, fontWeight: '700' }}>{items.length}</Text>
          </View>
          {items.map((m, i) => (
            <View key={m.id} style={[styles.row, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.border, paddingTop: 10, marginTop: 2 }]}>
              <Text style={{ color: t.text, flex: 1, fontSize: 15, lineHeight: 22 }}>{m.content}</Text>
              <Pressable
                hitSlop={8}
                onPress={() => Alert.alert('기억 지우기', m.content, [{ text: '취소' }, { text: '지우기', style: 'destructive', onPress: () => actions.deleteMemory(m.id) }])}
              >
                <Text style={{ color: t.subtext, fontSize: 20 }}>×</Text>
              </Pressable>
            </View>
          ))}
        </Card>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  catHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
});
