import { router } from 'expo-router';
import { Alert, Pressable, StyleSheet, Text, View } from 'react-native';

import { cancelWeeklyReport, scheduleWeeklyReport } from '../lib/notifications';
import { useStore } from '../lib/store';
import { useTheme } from '../lib/theme';
import { Body, Button, Card, Title } from './ui';

// expo weekday: 1=Sunday … 7=Saturday.
const DAYS: { label: string; value: number }[] = [
  { label: '월요일', value: 2 },
  { label: '금요일', value: 6 },
  { label: '일요일', value: 1 },
];

/** Weekly business review: pick a weekday for the 9am reminder, or generate it on demand. */
export function WeeklyReportCard() {
  const t = useTheme();
  const { state, actions } = useStore();

  const setDay = async (day: number | null) => {
    if (day === null) {
      await cancelWeeklyReport();
      actions.setWeeklyReportDay(null);
      return;
    }
    if (await scheduleWeeklyReport(day, 9)) actions.setWeeklyReportDay(day);
    else Alert.alert('알림 권한 필요', '휴대폰 설정에서 도로시의 알림을 허용해 주세요.');
  };

  return (
    <Card>
      <Title>주간 사업 리포트</Title>
      <Body muted>
        매주 지정한 요일 아침 9시에, 지난주 대비 변화(화물·고객 증감)와 이번 달 매출·수지 추세, 다음 주 챙길 것을 정리해 알려줘요. 아래 버튼으로 지금 바로 볼 수도 있어요.
      </Body>
      <View style={styles.row}>
        {[{ label: '끄기', value: null as number | null }, ...DAYS].map((d) => {
          const active = state.weeklyReportDay === d.value;
          return (
            <Pressable
              key={String(d.value)}
              onPress={() => setDay(d.value)}
              style={[styles.pill, { borderColor: active ? t.accent : t.border, backgroundColor: active ? t.accent : 'transparent' }]}
            >
              <Text style={{ color: active ? t.accentText : t.text }}>{d.label}</Text>
            </Pressable>
          );
        })}
      </View>
      <Button label="지금 리포트 보기" variant="ghost" onPress={() => router.navigate('/chat?weekly=1')} />
    </Card>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  pill: { borderWidth: 1, borderRadius: 16, paddingHorizontal: 14, paddingVertical: 8 },
});
