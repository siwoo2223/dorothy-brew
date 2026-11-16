import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Input, Title } from '../components/ui';
import { useStore } from '../lib/store';
import type { Followup, Routine, Task, TodoCandidate } from '../lib/types';
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
  const router = useRouter();
  const { state, actions } = useStore();
  const [title, setTitle] = useState('');
  const [preset, setPreset] = useState(0);

  const add = async () => {
    if (!title.trim()) return;
    await actions.addTask({ title: title.trim(), dueAt: PRESETS[preset].at()?.toISOString() });
    setTitle('');
    setPreset(0);
  };

  // Pick up to-dos the background check detected from watched rooms while the app was open.
  useFocusEffect(useCallback(() => { actions.reloadTodoCandidates(); }, [actions]));
  const candidates = state.todoCandidates ?? [];

  const byDue = (a: Task, b: Task) => (a.dueAt ?? '9999').localeCompare(b.dueAt ?? '9999');
  const open = state.tasks.filter((x) => !x.done).sort(byDue);
  const done = state.tasks.filter((x) => x.done).reverse();
  const openFollowups = (state.followups ?? []).filter((f) => !f.done).reverse();
  const routines = state.routines ?? [];

  // Group open tasks by urgency so the most pressing ones are on top.
  const now = new Date();
  const endOfToday = new Date(now); endOfToday.setHours(23, 59, 59, 999);
  const overdue = open.filter((x) => x.dueAt && new Date(x.dueAt) < now);
  const todayTasks = open.filter((x) => x.dueAt && new Date(x.dueAt) >= now && new Date(x.dueAt) <= endOfToday);
  const upcoming = open.filter((x) => x.dueAt && new Date(x.dueAt) > endOfToday);
  const undated = open.filter((x) => !x.dueAt);

  const taskRow = (task: Task) => (
    <TaskRow key={task.id} task={task} onToggle={() => actions.setTaskDone(task.id, true)} onDelete={() => actions.deleteTask(task.id)} />
  );

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
        <Button label="🌙 내일 할 일 미리 정리받기" variant="ghost" onPress={() => router.navigate({ pathname: '/chat', params: { plan: '1' } })} />
        <Body muted>자세한 시간은 도로시에게 말로 부탁해도 돼요. “금요일 오후 3시 치과 예약 알려줘”</Body>
      </Card>

      {candidates.length > 0 && (
        <>
          <Title>📥 도로시가 찾은 할 일 ({candidates.length})</Title>
          <Body muted>카톡 방에서 사장님께 온 요청이에요. 맞으면 등록, 아니면 무시하세요.</Body>
          {candidates.map((c) => (
            <CandidateRow key={c.id} c={c} onApprove={() => actions.approveTodoCandidate(c.id)} onDismiss={() => actions.dismissTodoCandidate(c.id)} />
          ))}
        </>
      )}

      {openFollowups.length > 0 && (
        <>
          <Title>✉️ 회신·부탁 ({openFollowups.length})</Title>
          <Body muted>답장할 사람이에요. “답장 문구” 버튼을 누르면 도로시가 바로 쓸 문장을 만들어 줘요.</Body>
          {openFollowups.map((f) => (
            <FollowupRow
              key={f.id}
              followup={f}
              onToggle={() => actions.completeFollowup(f.id)}
              onDelete={() => actions.deleteFollowup(f.id)}
              onDraft={() => router.navigate({ pathname: '/chat', params: { ask: `${f.who}님에게 "${f.what}" 관련 답장 문구를 한국어로 깔끔하게 하나 써줘. 바로 복사해서 보낼 수 있게.` } })}
            />
          ))}
        </>
      )}

      {routines.length > 0 && (
        <>
          <Title>🔁 반복 알림 ({routines.length})</Title>
          {routines.map((r) => (
            <RoutineRow key={r.id} routine={r} onDelete={() => actions.deleteRoutine(r.id)} />
          ))}
        </>
      )}

      {open.length === 0 && <Body muted>할 일이 없어요. 여유로운 하루 보내세요 🌿</Body>}

      {overdue.length > 0 && (
        <>
          <Title>⚠️ 기한 지남 ({overdue.length})</Title>
          {overdue.map(taskRow)}
        </>
      )}
      {todayTasks.length > 0 && (
        <>
          <Title>📍 오늘 ({todayTasks.length})</Title>
          {todayTasks.map(taskRow)}
        </>
      )}
      {upcoming.length > 0 && (
        <>
          <Title>🗓️ 예정 ({upcoming.length})</Title>
          {upcoming.map(taskRow)}
        </>
      )}
      {undated.length > 0 && (
        <>
          <Title>📌 기한 없음 ({undated.length})</Title>
          {undated.map(taskRow)}
        </>
      )}

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

const WEEKDAY_KR = ['', '일', '월', '화', '수', '목', '금', '토']; // expo weekday 1=Sun … 7=Sat
function RoutineRow({ routine, onDelete }: { routine: Routine; onDelete: () => void }) {
  const t = useTheme();
  const when =
    (routine.repeat === 'weekly' ? `매주 ${WEEKDAY_KR[routine.weekday ?? 1]}요일` : '매일') +
    ` ${String(routine.hour).padStart(2, '0')}:${String(routine.minute).padStart(2, '0')}`;
  return (
    <View style={[styles.row, { backgroundColor: t.card, borderColor: t.border }]}>
      <Text style={{ fontSize: 16 }}>🔁</Text>
      <View style={{ flex: 1 }}>
        <Text style={{ color: t.text, fontSize: 15 }}>{routine.title}</Text>
        <Text style={{ color: t.subtext, fontSize: 12 }}>{when}{routine.notificationId ? ' · 🔔' : ' · 알림 꺼짐'}</Text>
      </View>
      <Pressable onPress={onDelete} hitSlop={8}>
        <Text style={{ color: t.subtext, fontSize: 18 }}>×</Text>
      </Pressable>
    </View>
  );
}

function CandidateRow({ c, onApprove, onDismiss }: { c: TodoCandidate; onApprove: () => void; onDismiss: () => void }) {
  const t = useTheme();
  const when = c.dueAt
    ? new Date(c.dueAt).toLocaleString('ko-KR', { month: 'short', day: 'numeric', weekday: 'short', hour: 'numeric', minute: '2-digit' })
    : '시각 없음 → 회신 대기';
  return (
    <View style={[styles.row, { backgroundColor: t.card, borderColor: t.accent }]}>
      <View style={{ flex: 1 }}>
        <Text style={{ color: t.text, fontSize: 15, fontWeight: '600' }}>{c.title}</Text>
        <Text style={{ color: t.subtext, fontSize: 12 }}>{[c.room || c.who, when].filter(Boolean).join(' · ')}</Text>
      </View>
      <Pressable onPress={onApprove} hitSlop={6} style={[styles.draftBtn, { borderColor: t.accent, backgroundColor: t.accent }]}>
        <Text style={{ color: t.accentText, fontSize: 12, fontWeight: '700' }}>등록</Text>
      </Pressable>
      <Pressable onPress={onDismiss} hitSlop={8}>
        <Text style={{ color: t.subtext, fontSize: 18 }}>×</Text>
      </Pressable>
    </View>
  );
}

function FollowupRow({ followup, onToggle, onDelete, onDraft }: { followup: Followup; onToggle: () => void; onDelete: () => void; onDraft: () => void }) {
  const t = useTheme();
  return (
    <View style={[styles.row, { backgroundColor: t.card, borderColor: t.border }]}>
      <Pressable onPress={onToggle} hitSlop={8} style={[styles.check, { borderColor: t.accent, backgroundColor: 'transparent' }]} />
      <View style={{ flex: 1 }}>
        <Text style={{ color: t.text, fontSize: 15 }}>{followup.who}</Text>
        <Text style={{ color: t.subtext, fontSize: 12 }}>{followup.what}{followup.source ? ` · ${followup.source}` : ''}</Text>
      </View>
      <Pressable onPress={onDraft} hitSlop={6} style={[styles.draftBtn, { borderColor: t.accent }]}>
        <Text style={{ color: t.accent, fontSize: 12, fontWeight: '700' }}>답장 문구</Text>
      </Pressable>
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
  draftBtn: { borderWidth: 1, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6 },
});
