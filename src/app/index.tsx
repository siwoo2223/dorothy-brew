import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Body, Button, Card, Title } from '../components/ui';
import { briefing, describeError } from '../lib/assistant';
import { CallRecord, isMissed, syncCalls, todaysCalls, who as callWho } from '../lib/calls';
import { CalendarEvent, connectedEmails, listEvents } from '../lib/google';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import { autoBackupIfDue } from '../lib/recordings';
import { autoBackupIfDue as backupIfDue } from '../lib/backup';
import { checkForUpdate } from '../lib/update';
import { CompanySummary, companySummary, Overnight, overnightCounts } from '../lib/homeSummary';
import { voiceInputAvailable } from '../lib/voice';

function greeting(hour: number) {
  if (hour < 5) return '늦은 밤이에요';
  if (hour < 12) return '좋은 아침이에요';
  if (hour < 18) return '좋은 오후예요';
  return '편안한 저녁이에요';
}

// 저녁엔 하루를 마무리하며 '놓친 것'을 점검하도록 도로시에게 보내는 요청.
const MISSED_REVIEW =
  '오늘 내가 놓친 게 있는지 점검해줘. 기한이 오늘이거나 지난 할 일, 아직 회신 안 한 사람, 등록 안 한 할 일 후보, 부재중 전화를 짧게 모아서 내일로 넘길지 지금 처리할지 알려줘. 없으면 "오늘 놓친 건 없어요"라고만 해줘.';

export default function Today() {
  const t = useTheme();
  const router = useRouter();
  const { state, loaded, getState, actions } = useStore();
  const params = useLocalSearchParams<{ brief?: string }>();
  const [brief, setBrief] = useState('');
  const [loading, setLoading] = useState(false);
  const [events, setEvents] = useState<CalendarEvent[] | null>(null);
  const [updateBuild, setUpdateBuild] = useState<number | null>(null);
  const [autoBriefed, setAutoBriefed] = useState(false);
  const [calls, setCalls] = useState<CallRecord[]>([]);
  const [overnight, setOvernight] = useState<Overnight | null>(null);
  const [biz, setBiz] = useState<CompanySummary | null>(null);
  const insets = useSafeAreaInsets();

  useFocusEffect(
    useCallback(() => {
      overnightCounts().then(setOvernight).catch(() => {});
      companySummary().then(setBiz).catch(() => {});
      // 배경 점검이 올린 '할 일 후보'를 홈에서도 최신으로 반영.
      actions.reloadTodoCandidates?.();
    }, [actions]),
  );

  // Opened from the morning briefing notification (/?brief=1): build the briefing right away,
  // so tapping it is enough — Dorothy already "checked the phone" overnight.
  useFocusEffect(
    useCallback(() => {
      if (params.brief !== '1' || autoBriefed || !state.apiKey) return;
      setAutoBriefed(true);
      setLoading(true);
      briefing(getState())
        .then(setBrief)
        .catch((e) => setBrief(describeError(e)))
        .finally(() => setLoading(false));
    }, [params.brief, autoBriefed, state.apiKey, getState]),
  );

  useFocusEffect(
    useCallback(() => {
      checkForUpdate()
        .then((u) => setUpdateBuild(u.available ? (u.latest?.build ?? null) : null))
        .catch(() => {});
      // Back up any new call recordings in the background (no-op unless set up and enabled).
      autoBackupIfDue().catch(() => {});
      // 하루 한 번, 기억·할 일·설정을 구글 드라이브에 자동 백업(구글 연결 시).
      backupIfDue(getState()).catch(() => {});
      // Archive the phone's recent call log into the app (no-op without the call-log permission).
      syncCalls()
        .then(() => todaysCalls())
        .then(setCalls)
        .catch(() => {});
    }, []),
  );

  useFocusEffect(
    useCallback(() => {
      (async () => {
        if (!(await connectedEmails()).length) return setEvents(null);
        const start = new Date();
        start.setHours(0, 0, 0, 0);
        const end = new Date(start);
        end.setHours(23, 59, 59, 999);
        setEvents(await listEvents(start, end).catch(() => null));
      })();
    }, []),
  );

  if (!loaded) return <ActivityIndicator style={{ marginTop: 40 }} />;

  const now = new Date();
  const endOfToday = new Date(now);
  endOfToday.setHours(23, 59, 59, 999);
  const open = state.tasks.filter((x) => !x.done);
  const overdue = open.filter((x) => x.dueAt && new Date(x.dueAt) < now);
  const today = open.filter((x) => x.dueAt && new Date(x.dueAt) >= now && new Date(x.dueAt) <= endOfToday);
  const openFollowups = (state.followups ?? []).filter((x) => !x.done);
  const missedToday = calls.filter(isMissed);

  const addCallback = (c: CallRecord) => {
    actions.addFollowup({ who: c.name || c.number || '모르는 번호', what: '전화 회신', source: '부재중 전화' });
    Alert.alert('회신 대기 등록', `${c.name || c.number || '모르는 번호'}에게 전화 회신을 '할 일'에 넣었어요.`);
  };

  const runBriefing = async () => {
    setLoading(true);
    try {
      setBrief(await briefing(getState()));
    } catch (e) {
      setBrief(describeError(e));
    } finally {
      setLoading(false);
    }
  };

  const urgent = [...overdue, ...today];
  const timeLabel = (iso: string) => {
    const d = new Date(iso);
    const days = Math.round((new Date(now).setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 86400000);
    if (days === 1) return '어제';
    if (days > 1) return `${days}일 전`;
    return d.toLocaleTimeString('ko-KR', { hour: 'numeric', minute: d.getMinutes() ? '2-digit' : undefined });
  };
  const dotColor = (iso: string) => {
    const due = new Date(iso).getTime();
    if (due < now.getTime()) return t.danger;
    if (due - now.getTime() < 2 * 3600_000) return t.warn;
    return t.accent;
  };
  const briefSub = [
    overnight ? `밤사이 카톡 ${overnight.kakao}` : null,
    overnight ? `문자 ${overnight.sms}` : null,
    overnight ? `부재중 ${overnight.missed}` : null,
    events ? `오늘 일정 ${events.length}건` : `오늘 할 일 ${today.length}건`,
  ]
    .filter(Boolean)
    .join(' · ');

  const isEvening = now.getHours() >= 18;
  const candidates = state.todoCandidates ?? [];
  const missedSub =
    [
      urgent.length ? `오늘까지 할 일 ${urgent.length}건` : null,
      openFollowups.length ? `회신 ${openFollowups.length}명` : null,
      candidates.length ? `등록 대기 ${candidates.length}건` : null,
      missedToday.length ? `부재중 ${missedToday.length}건` : null,
    ]
      .filter(Boolean)
      .join(' · ') || '오늘 하루, 놓친 게 있나 같이 볼까요';

  return (
    <ScrollView contentContainerStyle={[styles.container, { paddingTop: insets.top + 16 }]}>
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={{ color: t.subtext, fontSize: 13 }}>
            {now.toLocaleDateString('ko-KR', { month: 'long', day: 'numeric', weekday: 'long' })}
          </Text>
          <Text style={[styles.hello, { color: t.text }]}>
            {greeting(now.getHours())}{state.profile.name ? `, ${state.profile.name}님` : ''}
          </Text>
        </View>
        {voiceInputAvailable() && (
          <Pressable
            accessibilityLabel="말하기"
            onPress={() => router.navigate({ pathname: '/chat', params: { voice: '1' } })}
            disabled={!state.apiKey}
            style={({ pressed }) => [styles.iconBtn, { backgroundColor: t.card, borderColor: t.border, opacity: !state.apiKey ? 0.5 : pressed ? 0.8 : 1 }]}
          >
            <Text style={{ fontSize: 18 }}>🎙️</Text>
          </Pressable>
        )}
        <Pressable
          accessibilityLabel="카메라로 보여주며 묻기"
          onPress={() => router.navigate('/camera')}
          disabled={!state.apiKey}
          style={({ pressed }) => [styles.iconBtn, { backgroundColor: t.card, borderColor: t.border, opacity: !state.apiKey ? 0.5 : pressed ? 0.8 : 1 }]}
        >
          <Text style={{ fontSize: 18 }}>📸</Text>
        </Pressable>
      </View>

      {updateBuild !== null && (
        <Card style={{ borderColor: t.accent }}>
          <Title>🔄 새 업데이트 (빌드 {updateBuild})</Title>
          <Body>앱에서 바로 받아 설치할 수 있어요.</Body>
          <Button label="업데이트 하러 가기" onPress={() => router.navigate('/settings')} />
        </Card>
      )}

      {!state.apiKey && (
        <Card style={{ borderColor: t.accent }}>
          <Title>시작하기</Title>
          <Body>도로시가 생각하고 대답하려면 Claude API 키가 필요해요. 설정에서 키와 내 소개를 입력해 주세요.</Body>
          <Button label="설정으로 가기" onPress={() => router.navigate('/settings')} />
        </Card>
      )}

      {/* ── 시간대별 히어로: 낮엔 '오늘의 브리핑', 저녁(18시~)엔 '오늘 마무리(놓친 것 점검)' ── */}
      {isEvening ? (
        <Pressable
          onPress={() => router.navigate({ pathname: '/chat', params: { ask: MISSED_REVIEW } })}
          disabled={!state.apiKey}
          style={({ pressed }) => [styles.hero, { backgroundColor: t.accent, opacity: !state.apiKey ? 0.6 : pressed ? 0.9 : 1 }]}
        >
          <View style={{ flex: 1, gap: 4 }}>
            <Text style={[styles.heroTitle, { color: t.accentText }]}>🌙 오늘 마무리</Text>
            <Text style={{ color: t.accentText, fontSize: 13, lineHeight: 19, opacity: 0.9 }}>{missedSub}</Text>
          </View>
          <Text style={{ color: t.accentText, fontSize: 24 }}>→</Text>
        </Pressable>
      ) : (
        <Pressable
          onPress={runBriefing}
          disabled={loading || !state.apiKey}
          style={({ pressed }) => [styles.hero, { backgroundColor: t.accent, opacity: pressed ? 0.9 : 1 }]}
        >
          <View style={{ flex: 1, gap: 4 }}>
            <Text style={[styles.heroTitle, { color: t.accentText }]}>☀️ 오늘의 브리핑</Text>
            <Text style={{ color: t.accentText, fontSize: 13, lineHeight: 19, opacity: 0.9 }}>{briefSub}</Text>
          </View>
          {loading ? <ActivityIndicator color={t.accentText} /> : <Text style={{ color: t.accentText, fontSize: 24 }}>→</Text>}
        </Pressable>
      )}

      {!!brief && !loading && (
        <Card>
          <Body>{brief}</Body>
        </Card>
      )}

      <View style={styles.stats}>
        <Stat label="기한 지남" value={overdue.length} color={overdue.length ? t.danger : t.subtext} onPress={() => router.navigate('/tasks')} />
        <Stat label="오늘" value={today.length} color={t.accent} onPress={() => router.navigate('/tasks')} />
        <Stat label="회신 대기" value={openFollowups.length} color={t.subtext} onPress={() => router.navigate('/tasks')} />
      </View>

      {/* ── 등록 대기: 카톡 할 일 감지가 올린 후보. 등록/무시는 할 일 탭 맨 위에서. ── */}
      {candidates.length > 0 && (
        <Pressable
          onPress={() => router.navigate('/tasks')}
          style={({ pressed }) => [styles.pending, { backgroundColor: t.accentSoft, borderColor: t.accent, opacity: pressed ? 0.85 : 1 }]}
        >
          <Text style={{ fontSize: 18 }}>📥</Text>
          <View style={{ flex: 1 }}>
            <Text style={{ color: t.text, fontSize: 14, fontWeight: '700' }}>등록할 할 일 {candidates.length}건</Text>
            <Text style={{ color: t.subtext, fontSize: 12 }} numberOfLines={1}>
              {candidates.slice(0, 2).map((c) => c.title).join(' · ')} — 등록/무시 확인하세요
            </Text>
          </View>
          <Text style={{ color: t.accent, fontSize: 20 }}>→</Text>
        </Pressable>
      )}

      {/* ── 놓치지 마세요 ── */}
      {urgent.length > 0 && (
        <>
          <Text style={[styles.section, { color: t.text }]}>놓치지 마세요</Text>
          <View style={[styles.listCard, { backgroundColor: t.card, borderColor: t.border }]}>
            {urgent.slice(0, 6).map((task, i) => (
              <View key={task.id} style={[styles.listRow, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.border }]}>
                <View style={[styles.dot, { backgroundColor: dotColor(task.dueAt!) }]} />
                <Text style={[styles.listText, { color: t.text }]} numberOfLines={1}>{task.title}</Text>
                <Text style={{ color: t.subtext, fontSize: 13 }}>{timeLabel(task.dueAt!)}</Text>
                <Pressable onPress={() => actions.setTaskDone(task.id, true)} hitSlop={10} style={[styles.doneBtn, { borderColor: t.accent }]}>
                  <Text style={{ color: t.accent, fontSize: 13, fontWeight: '700' }}>✓ 완료</Text>
                </Pressable>
              </View>
            ))}
          </View>
        </>
      )}

      {/* ── 회사 현황 ── */}
      {biz && (
        <>
          <Text style={[styles.section, { color: t.text }]}>회사 현황</Text>
          <Pressable
            onPress={() => router.navigate({ pathname: '/chat', params: { company: '1' } })}
            style={({ pressed }) => [styles.listCard, { backgroundColor: t.card, borderColor: t.border, opacity: pressed ? 0.85 : 1 }]}
          >
            <View style={styles.bizRow}>
              <Text style={{ color: t.subtext, fontSize: 14, flex: 1 }}>{biz.line}</Text>
              {biz.badge && (
                <View style={[styles.badge, { backgroundColor: t.accentSoft }]}>
                  <Text style={{ color: t.accent, fontSize: 13 }}>
                    이번 달 <Text style={{ fontWeight: '800' }}>{biz.badge}</Text>
                  </Text>
                </View>
              )}
            </View>
            {biz.next && (
              <Text style={[styles.bizNext, { color: t.subtext, borderTopColor: t.border }]}>다음 출항 · {biz.next}</Text>
            )}
          </Pressable>
        </>
      )}

      {events && events.length > 0 && (
        <Card>
          <Title>📅 오늘 일정</Title>
          {events.map((e) => (
            <Body key={e.id}>
              <Text style={{ color: t.accent, fontWeight: '600' }}>
                {e.allDay ? '종일' : new Date(e.start).toLocaleTimeString('ko-KR', { hour: 'numeric', minute: '2-digit' })}
              </Text>{' '}
              {e.title}
              {e.location ? <Text style={{ color: t.subtext }}> · {e.location}</Text> : null}
            </Body>
          ))}
        </Card>
      )}

      {openFollowups.length > 0 && (
        <Card>
          <Title>✉️ 회신·부탁 ({openFollowups.length})</Title>
          {openFollowups.slice(0, 6).map((f) => (
            <Body key={f.id}>
              • {f.who} <Text style={{ color: t.subtext }}>— {f.what}{f.source ? ` (${f.source})` : ''}</Text>
            </Body>
          ))}
          {openFollowups.length > 6 && <Body muted>외 {openFollowups.length - 6}건 · 할 일 탭에서 모두 보기</Body>}
        </Card>
      )}

      {calls.length > 0 && (
        <Card>
          <Title>📞 오늘 통화 ({calls.length})</Title>
          {missedToday.length > 0 && (
            <Text style={[styles.callText, { color: t.danger }]}>부재중·거절 {missedToday.length}건 — 회신 챙기세요</Text>
          )}
          {calls.slice(0, 8).map((c) => (
            <View key={c.id} style={styles.callRow}>
              <Text style={[styles.callText, { flex: 1, color: t.text }]}>
                {isMissed(c) ? '⚠️' : '•'} {callWho(c)}{' '}
                <Text style={{ color: t.subtext }}>
                  {new Date(c.time).toLocaleTimeString('ko-KR', { hour: 'numeric', minute: '2-digit' })}
                </Text>
              </Text>
              {isMissed(c) && (
                <Text style={{ color: t.accent, fontWeight: '600' }} onPress={() => addCallback(c)}>
                  회신 등록
                </Text>
              )}
            </View>
          ))}
          {calls.length > 8 && <Body muted>외 {calls.length - 8}건 · 도로시에게 &quot;오늘 통화 누구누구?&quot;로 물어보세요</Body>}
        </Card>
      )}
    </ScrollView>
  );
}

function Stat({ label, value, color, onPress }: { label: string; value: number; color: string; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.stat, { backgroundColor: t.card, borderColor: t.border, opacity: pressed ? 0.85 : 1 }]}
    >
      <Text style={[styles.statValue, { color }]}>{value}</Text>
      <Text style={{ color: t.subtext, fontSize: 13 }}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { paddingHorizontal: 16, paddingBottom: 24, gap: 14 },
  header: { flexDirection: 'row', alignItems: 'flex-end', gap: 8 },
  hello: { fontSize: 24, fontWeight: '800', letterSpacing: -0.5, marginTop: 2 },
  iconBtn: { width: 44, height: 44, borderRadius: 22, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  hero: { flexDirection: 'row', alignItems: 'center', gap: 12, borderRadius: 22, paddingVertical: 18, paddingHorizontal: 20 },
  heroTitle: { fontSize: 18, fontWeight: '800', letterSpacing: -0.3 },
  stats: { flexDirection: 'row', gap: 8 },
  stat: { flex: 1, borderRadius: 18, borderWidth: StyleSheet.hairlineWidth, paddingVertical: 14, alignItems: 'center', gap: 2 },
  statValue: { fontSize: 24, fontWeight: '800', letterSpacing: -0.5 },
  section: { fontSize: 15, fontWeight: '800', letterSpacing: -0.2, marginTop: 4, marginBottom: -4 },
  listCard: { borderRadius: 18, borderWidth: StyleSheet.hairlineWidth, overflow: 'hidden' },
  listRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 16, minHeight: 50 },
  doneBtn: { borderWidth: 1, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 5 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  listText: { flex: 1, fontSize: 15, fontWeight: '600' },
  pending: { flexDirection: 'row', alignItems: 'center', gap: 12, borderRadius: 18, borderWidth: 1, paddingHorizontal: 16, paddingVertical: 14 },
  bizRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 16, paddingVertical: 14 },
  badge: { borderRadius: 16, paddingHorizontal: 12, paddingVertical: 6 },
  bizNext: { fontSize: 13, paddingHorizontal: 16, paddingVertical: 12, borderTopWidth: StyleSheet.hairlineWidth },
  callRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  callText: { fontSize: 15, lineHeight: 22 },
});
