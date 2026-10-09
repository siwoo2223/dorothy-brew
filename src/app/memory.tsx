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
  };

  const groups = new Map<string, typeof state.memories>();
  for (const m of state.memories) groups.set(m.category, [...(groups.get(m.category) ?? []), m]);

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Body muted>
        도로시가 알고 있는 나에 대한 사실들이에요. 대화하면서 자동으로 쌓이고, 여기서 직접 추가하거나 지울 수 있어요. 모든 기억은 이 휴대폰에만 저장돼요.
      </Body>
      <Card>
        <Input value={category} onChangeText={setCategory} placeholder="분류 (예: 가족, 건강, 취향)" />
        <Input value={content} onChangeText={setContent} placeholder="기억할 내용 (예: 커피는 디카페인만 마심)" multiline />
        <Button label="기억하기" onPress={add} disabled={!content.trim()} />
      </Card>

      {state.memories.length === 0 && <Body muted>아직 기억이 없어요. 도로시에게 나에 대해 이야기해 보세요.</Body>}
      {[...groups.entries()].map(([cat, items]) => (
        <Card key={cat}>
          <Title>{cat}</Title>
          {items.map((m) => (
            <View key={m.id} style={styles.row}>
              <Text style={{ color: t.text, flex: 1, fontSize: 15, lineHeight: 22 }}>{m.content}</Text>
              <Pressable
                hitSlop={8}
                onPress={() => Alert.alert('기억 지우기', m.content, [{ text: '취소' }, { text: '지우기', style: 'destructive', onPress: () => actions.deleteMemory(m.id) }])}
              >
                <Text style={{ color: t.subtext, fontSize: 18 }}>×</Text>
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
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
});
