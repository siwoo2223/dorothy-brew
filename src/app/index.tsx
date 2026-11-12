import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Title } from '../components/ui';
import { briefing, describeError } from '../lib/assistant';
import { CallRecord, isMissed, syncCalls, todaysCalls, who as callWho } from '../lib/calls';
import { CalendarEvent, connectedEmails, listEvents } from '../lib/google';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import { autoBackupIfDue } from '../lib/recordings';
import { checkForUpdate } from '../lib/update';
import { voiceInputAvailable } from '../lib/voice';

function greeting(hour: number) {
  if (hour < 5) return '늦은 밤이에요';
  if (hour < 12) return '좋은 아침이에요';
  if (hour < 18) return '좋은 오후예요';
  return '편안한 저녁이에요';
}

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
  const undated = open.filter((x) => !x.dueAt);
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

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={[styles.hello, { color: t.text }]}>
        {greeting(now.getHours())}{state.profile.name ? `, ${state.profile.name}님` : ''}
      </Text>
      <Body muted>{now.toLocaleDateString('ko-KR', { dateStyle: 'full' })}</Body>

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

      <View style={styles.stats}>
        <Stat label="기한 지남" value={overdue.length} color={overdue.length ? t.danger : t.subtext} />
        <Stat label="오늘" value={today.length} color={t.accent} />
        <Stat label="언젠가" value={undated.length} color={t.subtext} />
      </View>

      {events && (
        <Card>
          <Title>📅 오늘 일정</Title>
          {events.length === 0 && <Body muted>오늘은 캘린더 일정이 없어요.</Body>}
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

      {[...overdue, ...today].length > 0 && (
        <Card>
          <Title>놓치지 마세요</Title>
          {[...overdue, ...today].map((task) => (
            <Body key={task.id}>
              {new Date(task.dueAt!) < now ? '⚠️' : '•'} {task.title}{' '}
              <Text style={{ color: t.subtext }}>
                {new Date(task.dueAt!).toLocaleTimeString('ko-KR', { hour: 'numeric', minute: '2-digit' })}
              </Text>
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

      <Card>
        <Title>오늘의 브리핑</Title>
        {loading ? <ActivityIndicator /> : brief ? <Body>{brief}</Body> : <Body muted>밤사이 온 메시지·문자·부재중 전화와 오늘 일정, 회사 현황, 할 일, 날씨를 모아 하루를 정리해 드려요.</Body>}
        <Button label={brief ? '다시 브리핑 받기' : '브리핑 받기'} onPress={runBriefing} disabled={loading || !state.apiKey} />
      </Card>

      <Card>
        <Title>🌙 내일 할 일</Title>
        <Body muted>오늘 받은 카톡·문자·메신저, 통화, 일정을 모아 내일 해야 할 일을 정리해요. 메시지와 통화는 설정 → 메시지·통화에서 먼저 연결해 주세요.</Body>
        <Button label="내일 할 일 정리하기" onPress={() => router.navigate({ pathname: '/chat', params: { plan: '1' } })} />
      </Card>

      <View style={styles.talkRow}>
        <View style={{ flex: 1 }}>
          <Button label="💬 글로 말 걸기" variant="ghost" onPress={() => router.navigate('/chat')} />
        </View>
        {voiceInputAvailable() && (
          <View style={{ flex: 1 }}>
            <Button label="🎙️ 말로 하기" onPress={() => router.navigate({ pathname: '/chat', params: { voice: '1' } })} disabled={!state.apiKey} />
          </View>
        )}
      </View>
    </ScrollView>
  );
}

function Stat({ label, value, color }: { label: string; value: number; color: string }) {
  const t = useTheme();
  return (
    <View style={[styles.stat, { backgroundColor: t.card, borderColor: t.border }]}>
      <Text style={[styles.statValue, { color }]}>{value}</Text>
      <Text style={{ color: t.subtext, fontSize: 13 }}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 14 },
  hello: { fontSize: 26, fontWeight: '800' },
  stats: { flexDirection: 'row', gap: 10 },
  stat: { flex: 1, borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, padding: 14, alignItems: 'center' },
  statValue: { fontSize: 24, fontWeight: '800' },
  talkRow: { flexDirection: 'row', gap: 10 },
  callRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  callText: { fontSize: 15, lineHeight: 22 },
});
