import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Button, Card, Title } from '../components/ui';
import { briefing, describeError } from '../lib/assistant';
import { CalendarEvent, connectedEmail, listEvents } from '../lib/google';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
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
  const { state, loaded, getState } = useStore();
  const [brief, setBrief] = useState('');
  const [loading, setLoading] = useState(false);
  const [events, setEvents] = useState<CalendarEvent[] | null>(null);

  useFocusEffect(
    useCallback(() => {
      (async () => {
        if (!(await connectedEmail())) return setEvents(null);
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

      <Card>
        <Title>오늘의 브리핑</Title>
        {loading ? <ActivityIndicator /> : brief ? <Body>{brief}</Body> : <Body muted>할 일, 기억, 날씨를 모아 오늘 하루를 정리해 드려요.</Body>}
        <Button label={brief ? '다시 브리핑 받기' : '브리핑 받기'} onPress={runBriefing} disabled={loading || !state.apiKey} />
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
});
