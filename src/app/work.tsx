import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import MemoryScreen from './memory';
import Tasks from './tasks';
import { useTheme } from '../lib/theme';

type Tab = 'tasks' | 'memory';
const TABS: { key: Tab; label: string }[] = [
  { key: 'tasks', label: '✅ 할 일' },
  { key: 'memory', label: '🧠 기억' },
];

/**
 * '할 일 · 기억'을 한 탭으로. 위 세그먼트로 할 일/기억을 전환한다(각 화면은 기존 것을 그대로 재사용).
 * 탭을 6개→4개로 줄이는 1단계 재구성의 일부.
 */
export default function Work() {
  const t = useTheme();
  const [tab, setTab] = useState<Tab>('tasks');
  return (
    <View style={{ flex: 1, backgroundColor: t.bg }}>
      <View style={styles.seg}>
        {TABS.map(({ key, label }) => {
          const on = key === tab;
          return (
            <Pressable
              key={key}
              onPress={() => setTab(key)}
              style={[styles.segBtn, { backgroundColor: on ? t.accent : t.card, borderColor: on ? t.accent : t.border }]}
            >
              <Text style={{ color: on ? t.accentText : t.subtext, fontWeight: '800', fontSize: 14 }}>{label}</Text>
            </Pressable>
          );
        })}
      </View>
      <View style={{ flex: 1 }}>{tab === 'tasks' ? <Tasks /> : <MemoryScreen />}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  seg: { flexDirection: 'row', gap: 8, paddingHorizontal: 16, paddingTop: 12, paddingBottom: 4 },
  segBtn: { flex: 1, alignItems: 'center', paddingVertical: 10, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth },
});
