import { useFocusEffect, useNavigation } from 'expo-router';
import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Card } from '../components/ui';
import { companyConfigured } from '../lib/company';
import { syncCarrierSchedule } from '../lib/sailingSync';
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
  const [syncing, setSyncing] = useState(false);
  const lastSync = useRef(0);

  useLayoutEffect(() => {
    navigation.setOptions({ title: '출항 스케줄' });
  }, [navigation]);

  const doSync = useCallback(async (silent: boolean) => {
    if (!companyConfigured() || syncing) return;
    setSyncing(true);
    try {
      const r = await syncCarrierSchedule(actions);
      lastSync.current = Date.now();
      if (!silent) {
        Alert.alert(
          '선사 스케줄 갱신',
          r.total === 0
            ? '서버에 아직 자동 수집된 선편이 없어요. (PC 수집이 아직 안 돌았을 수 있어요)'
            : `새로 ${r.added}건 · 변경 ${r.updated}건 반영했어요 (총 ${r.total}건 확인).`,
        );
      }
    } catch (e) {
      if (!silent) Alert.alert('갱신 실패', e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing(false);
    }
  }, [actions, syncing]);

  // 화면에 들어올 때 자동 동기화(10분에 한 번까지). 서버가 매일 수집해 둔 최신 선편을 조용히 받아 온다.
  useFocusEffect(
    useCallback(() => {
      if (companyConfigured() && Date.now() - lastSync.current > 10 * 60 * 1000) doSync(true);
    }, [doSync]),
  );

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

  const Header = (
    <View style={styles.headerRow}>
      <View style={[styles.toggle, { borderColor: t.border }]}>
        {(['list', 'calendar'] as const).map((m) => (
          <Pressable key={m} onPress={() => setMode(m)} style={[styles.toggleBtn, mode === m && { backgroundColor: t.accent }]}>
            <Text style={{ color: mode === m ? t.accentText : t.text, fontWeight: '700', fontSize: 13 }}>{m === 'list' ? '📋 리스트' : '📅 캘린더'}</Text>
          </Pressable>
        ))}
      </View>
      {companyConfigured() && (
        <Pressable onPress={() => doSync(false)} disabled={syncing} style={[styles.refresh, { borderColor: t.border, backgroundColor: t.card }]}>
          {syncing ? <ActivityIndicator size="small" color={t.accent} /> : <Text style={{ color: t.accent, fontSize: 13, fontWeight: '700' }}>🔄 선사 새로고침</Text>}
        </Pressable>
      )}
    </View>
  );

  if (sailings.length === 0) {
    return (
      <ScrollView contentContainerStyle={styles.container}>
        {Header}
        <Card>
          <Body muted>
            아직 저장된 출항 스케줄이 없어요. 위 **🔄 선사 새로고침**을 누르면 서버에 매일 자동 수집된 부산→마닐라 선편을 불러와요. 또는 채팅에서 **🚢 부킹 스케줄** 버튼으로 기나글로벌 스케줄 사진을 올리면, 도로시가 선사·항차·마감·출항일을 읽어 여기에 저장해요.
          </Body>
        </Card>
      </ScrollView>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      {Header}
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
        <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <Text style={{ color: t.text, fontSize: 16, fontWeight: '800' }}>
            {s.carrier}{s.voyageNo ? ` · ${s.voyageNo}` : ''}
          </Text>
          {s.transship === false && <Badge text="직항" bg="#2E7D5B" />}
          {s.transship === true && <Badge text="환적" bg="#C9792E" />}
          {s.source === 'auto' && <Badge text="자동" bg={t.border} fg={t.subtext} />}
        </View>
        <Pressable onPress={onDelete} hitSlop={8}><Text style={{ color: t.subtext, fontSize: 18 }}>×</Text></Pressable>
      </View>
      {!!s.vessel && <Text style={{ color: t.text, fontSize: 13 }}>🚢 {s.vessel}{s.service ? ` · ${s.service}` : ''}{s.transitDays != null ? ` · ${s.transitDays}일` : ''}</Text>}
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

function Badge({ text, bg, fg = '#fff' }: { text: string; bg: string; fg?: string }) {
  return (
    <View style={{ backgroundColor: bg, borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2 }}>
      <Text style={{ color: fg, fontSize: 10, fontWeight: '800' }}>{text}</Text>
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
  headerRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, flexWrap: 'wrap' },
  refresh: { borderWidth: 1, borderRadius: 14, paddingHorizontal: 14, paddingVertical: 8, minWidth: 110, alignItems: 'center' },
  toggle: { flexDirection: 'row', borderWidth: 1, borderRadius: 14, padding: 3, gap: 3 },
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
