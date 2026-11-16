import { useNavigation } from 'expo-router';
import { useLayoutEffect, useMemo, useState } from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Card } from '../components/ui';
import { useStore } from '../lib/store';
import type { Sailing } from '../lib/types';
import { useTheme } from '../lib/theme';

/** The date a sailing is placed on in the calendar: ETD first, then cut-offs, then ETA. */
const primaryDate = (s: Sailing) => s.etd || s.cyCutoff || s.docCutoff || s.eta || '';
const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const mdLabel = (iso?: string) => (iso && iso.length >= 10 ? iso.slice(5, 10).replace('-', '/') : iso || '');
const WEEK = ['일', '월', '화', '수', '목', '금', '토'];

export default function Schedule() {
  const t = useTheme();
  const navigation = useNavigation();
  const { state, actions } = useStore();
  const sailings = state.sailings ?? [];

  const [mode, setMode] = useState<'list' | 'calendar'>('list');
  const [cursor, setCursor] = useState(() => new Date());
  const [selected, setSelected] = useState<string | null>(null);

  useLayoutEffect(() => {
    navigation.setOptions({ title: '출항 스케줄' });
  }, [navigation]);

  // Group sailings by their primary date (YYYY-MM-DD).
  const byDate = useMemo(() => {
    const m = new Map<string, Sailing[]>();
    for (const s of sailings) {
      const key = primaryDate(s);
      if (!key) continue;
      m.set(key, [...(m.get(key) ?? []), s]);
    }
    return m;
  }, [sailings]);

  const sortedList = useMemo(
    () => [...sailings].sort((a, b) => (primaryDate(a) || '9999').localeCompare(primaryDate(b) || '9999')),
    [sailings],
  );

  const remove = (s: Sailing) =>
    Alert.alert('스케줄 삭제', `${s.carrier}${s.voyageNo ? ` ${s.voyageNo}` : ''} 삭제할까요?`, [
      { text: '취소' },
      { text: '삭제', style: 'destructive', onPress: () => actions.deleteSailing(s.id) },
    ]);

  const Toggle = (
    <View style={[styles.toggle, { borderColor: t.border }]}>
      {(['list', 'calendar'] as const).map((m) => (
        <Pressable key={m} onPress={() => setMode(m)} style={[styles.toggleBtn, mode === m && { backgroundColor: t.accent }]}>
          <Text style={{ color: mode === m ? t.accentText : t.text, fontWeight: '700', fontSize: 13 }}>{m === 'list' ? '📋 리스트' : '📅 캘린더'}</Text>
        </Pressable>
      ))}
    </View>
  );

  if (sailings.length === 0) {
    return (
      <ScrollView contentContainerStyle={styles.container}>
        {Toggle}
        <Card>
          <Body muted>
            아직 저장된 출항 스케줄이 없어요. 채팅에서 **🚢 부킹 스케줄** 버튼으로 기나글로벌 스케줄 사진을 올리면, 도로시가 선사·항차·마감·출항일을 읽어 여기에 저장해요.
          </Body>
        </Card>
      </ScrollView>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      {Toggle}
      {mode === 'list' ? (
        sortedList.map((s) => <SailingCard key={s.id} s={s} t={t} onDelete={() => remove(s)} />)
      ) : (
        <CalendarView cursor={cursor} setCursor={setCursor} byDate={byDate} selected={selected} setSelected={setSelected} t={t} onDelete={remove} />
      )}
    </ScrollView>
  );
}

function SailingCard({ s, t, onDelete }: { s: Sailing; t: ReturnType<typeof useTheme>; onDelete: () => void }) {
  const tag = (label: string, v?: string, danger?: boolean) =>
    v ? (
      <Text style={{ color: danger ? t.danger : t.subtext, fontSize: 12 }}>
        {label} <Text style={{ fontWeight: '700', color: danger ? t.danger : t.text }}>{mdLabel(v)}</Text>
      </Text>
    ) : null;
  return (
    <View style={[styles.sail, { backgroundColor: t.card, borderColor: t.border }]}>
      <View style={styles.sailTop}>
        <Text style={{ color: t.text, fontSize: 16, fontWeight: '800' }}>
          {s.carrier}{s.voyageNo ? ` · ${s.voyageNo}` : ''}
        </Text>
        <Pressable onPress={onDelete} hitSlop={8}><Text style={{ color: t.subtext, fontSize: 18 }}>×</Text></Pressable>
      </View>
      {(s.from || s.to) && <Text style={{ color: t.text, fontSize: 14 }}>{[s.from, s.to].filter(Boolean).join(' → ')}</Text>}
      <View style={styles.tags}>
        {tag('서류마감', s.docCutoff, true)}
        {tag('반입마감', s.cyCutoff, true)}
        {tag('출항', s.etd)}
        {tag('도착', s.eta)}
      </View>
      {!!s.note && <Text style={{ color: t.subtext, fontSize: 12 }}>{s.note}</Text>}
    </View>
  );
}

function CalendarView({
  cursor, setCursor, byDate, selected, setSelected, t, onDelete,
}: {
  cursor: Date; setCursor: (d: Date) => void; byDate: Map<string, Sailing[]>;
  selected: string | null; setSelected: (s: string | null) => void;
  t: ReturnType<typeof useTheme>; onDelete: (s: Sailing) => void;
}) {
  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  const first = new Date(year, month, 1);
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const lead = first.getDay(); // 0=Sun
  const cells: (number | null)[] = [...Array(lead).fill(null), ...Array.from({ length: daysInMonth }, (_, i) => i + 1)];
  while (cells.length % 7 !== 0) cells.push(null);
  const todayKey = ymd(new Date());

  const move = (delta: number) => { setCursor(new Date(year, month + delta, 1)); setSelected(null); };
  const dayItems = selected ? byDate.get(selected) ?? [] : [];

  return (
    <>
      <View style={styles.calHead}>
        <Pressable onPress={() => move(-1)} hitSlop={10}><Text style={{ color: t.accent, fontSize: 22 }}>‹</Text></Pressable>
        <Text style={{ color: t.text, fontSize: 16, fontWeight: '800' }}>{year}년 {month + 1}월</Text>
        <Pressable onPress={() => move(1)} hitSlop={10}><Text style={{ color: t.accent, fontSize: 22 }}>›</Text></Pressable>
      </View>
      <View style={styles.weekRow}>
        {WEEK.map((w, i) => (
          <Text key={w} style={{ flex: 1, textAlign: 'center', fontSize: 12, color: i === 0 ? t.danger : t.subtext }}>{w}</Text>
        ))}
      </View>
      <View style={styles.grid}>
        {cells.map((day, i) => {
          if (day === null) return <View key={i} style={styles.cell} />;
          const key = ymd(new Date(year, month, day));
          const items = byDate.get(key);
          const isSel = selected === key;
          const isToday = key === todayKey;
          return (
            <Pressable key={i} onPress={() => setSelected(isSel ? null : key)} style={[styles.cell, isSel && { backgroundColor: t.accentSoft, borderRadius: 10 }]}>
              <Text style={{ color: isToday ? t.accent : t.text, fontSize: 13, fontWeight: isToday ? '800' : '500' }}>{day}</Text>
              {items && <View style={[styles.cellDot, { backgroundColor: t.accent }]}><Text style={{ color: t.accentText, fontSize: 9, fontWeight: '800' }}>{items.length}</Text></View>}
            </Pressable>
          );
        })}
      </View>
      {selected ? (
        dayItems.length ? (
          <>
            <Text style={[styles.section, { color: t.text }]}>{mdLabel(selected)} 출항 {dayItems.length}척</Text>
            {dayItems.map((s) => <SailingCard key={s.id} s={s} t={t} onDelete={() => onDelete(s)} />)}
          </>
        ) : (
          <Body muted>{mdLabel(selected)}에는 저장된 스케줄이 없어요.</Body>
        )
      ) : (
        <Body muted>날짜를 누르면 그날 출항하는 배가 보여요. (숫자 = 그날 척수)</Body>
      )}
    </>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 10 },
  toggle: { flexDirection: 'row', borderWidth: 1, borderRadius: 14, padding: 3, alignSelf: 'center', gap: 3 },
  toggleBtn: { paddingHorizontal: 18, paddingVertical: 8, borderRadius: 11 },
  sail: { borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, padding: 14, gap: 5 },
  sailTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  tags: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginTop: 2 },
  calHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 8, paddingVertical: 6 },
  weekRow: { flexDirection: 'row' },
  grid: { flexDirection: 'row', flexWrap: 'wrap' },
  cell: { width: `${100 / 7}%`, aspectRatio: 1, alignItems: 'center', justifyContent: 'center', gap: 2 },
  cellDot: { minWidth: 16, height: 16, borderRadius: 8, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 3 },
  section: { fontSize: 15, fontWeight: '800', marginTop: 8 },
});
