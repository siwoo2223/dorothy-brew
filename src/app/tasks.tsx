import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Input, Title } from '../components/ui';
import { useStore } from '../lib/store';
import type { Task } from '../lib/types';
import { useTheme } from '../lib/theme';

type Preset = { label: string; at: () => Date | undefined };

const at = (daysFromToday: number, hour: number) => {
  const d = new Date();
  d.setDate(d.getDate() + daysFromToday);
  d.setHours(hour, 0, 0, 0);
  return d;
};

const PRESETS: Preset[] = [
  { label: '기한 없음', at: () => undefined },
  { label: '1시간 후', at: () => new Date(Date.now() + 60 * 60 * 1000) },
  { label: '오늘 저녁 9시', at: () => at(0, 21) },
  { label: '내일 아침 9시', at: () => at(1, 9) },
];

export default function Tasks() {
  const t = useTheme();
  const { state, actions } = useStore();
  const [title, setTitle] = useState('');
  const [preset, setPreset] = useState(0);

  const add = async () => {
    if (!title.trim()) return;
    await actions.addTask({ title: title.trim(), dueAt: PRESETS[preset].at()?.toISOString() });
    setTitle('');
    setPreset(0);
  };

  const byDue = (a: Task, b: Task) => (a.dueAt ?? '9999').localeCompare(b.dueAt ?? '9999');
  const open = state.tasks.filter((x) => !x.done).sort(byDue);
  const done = state.tasks.filter((x) => x.done).reverse();

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Card>
        <Input value={title} onChangeText={setTitle} placeholder="할 일 추가 (예: 엄마한테 전화)" onSubmitEditing={add} returnKeyType="done" />
        <View style={styles.presets}>
          {PRESETS.map((p, i) => (
            <Pressable
              key={p.label}
              onPress={() => setPreset(i)}
              style={[styles.preset, { borderColor: i === preset ? t.accent : t.border, backgroundColor: i === preset ? t.accent : 'transparent' }]}
            >
              <Text style={{ color: i === preset ? t.accentText : t.text, fontSize: 13 }}>{p.label}</Text>
            </Pressable>
          ))}
        </View>
        <Button label="추가" onPress={add} disabled={!title.trim()} />
        <Body muted>자세한 시간은 도로시에게 말로 부탁해도 돼요. "금요일 오후 3시 치과 예약 알려줘"</Body>
      </Card>

      <Title>진행 중 ({open.length})</Title>
      {open.length === 0 && <Body muted>할 일이 없어요. 여유로운 하루 보내세요 🌿</Body>}
      {open.map((task) => (
        <TaskRow key={task.id} task={task} onToggle={() => actions.setTaskDone(task.id, true)} onDelete={() => actions.deleteTask(task.id)} />
      ))}

      {done.length > 0 && <Title>완료 ({done.length})</Title>}
      {done.map((task) => (
        <TaskRow key={task.id} task={task} onToggle={() => actions.setTaskDone(task.id, false)} onDelete={() => actions.deleteTask(task.id)} />
      ))}
    </ScrollView>
  );
}

function TaskRow({ task, onToggle, onDelete }: { task: Task; onToggle: () => void; onDelete: () => void }) {
  const t = useTheme();
  const overdue = !task.done && task.dueAt && new Date(task.dueAt) < new Date();
  return (
    <View style={[styles.row, { backgroundColor: t.card, borderColor: t.border }]}>
      <Pressable onPress={onToggle} hitSlop={8} style={[styles.check, { borderColor: t.accent, backgroundColor: task.done ? t.accent : 'transparent' }]}>
        {task.done && <Text style={{ color: '#fff', fontSize: 13 }}>✓</Text>}
      </Pressable>
      <View style={{ flex: 1 }}>
        <Text style={{ color: task.done ? t.subtext : t.text, fontSize: 15, textDecorationLine: task.done ? 'line-through' : 'none' }}>{task.title}</Text>
        {task.dueAt && (
          <Text style={{ color: overdue ? t.danger : t.subtext, fontSize: 12 }}>
            {overdue ? '기한 지남 · ' : task.notificationId ? '🔔 ' : ''}
            {new Date(task.dueAt).toLocaleString('ko-KR', { month: 'short', day: 'numeric', weekday: 'short', hour: 'numeric', minute: '2-digit' })}
          </Text>
        )}
        {task.notes ? <Text style={{ color: t.subtext, fontSize: 12 }}>{task.notes}</Text> : null}
      </View>
      <Pressable onPress={onDelete} hitSlop={8}>
        <Text style={{ color: t.subtext, fontSize: 18 }}>×</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 10 },
  presets: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  preset: { borderWidth: 1, borderRadius: 16, paddingHorizontal: 10, paddingVertical: 6 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth },
  check: { width: 22, height: 22, borderRadius: 11, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
});
