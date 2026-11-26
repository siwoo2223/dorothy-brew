import type { ErrorBoundaryProps } from 'expo-router';
import { router } from 'expo-router';
import { Tabs } from 'expo-router/js-tabs';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { Pressable, StyleSheet, Text, useColorScheme, View } from 'react-native';
import { KeyboardProvider } from 'react-native-keyboard-controller';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import '../lib/notifications';
import { loadEleven } from '../lib/elevenlabs';
import { ensureProactiveRegistered } from '../lib/proactive';
import { NotificationRouter } from '../components/NotificationRouter';
import { VoiceLauncher } from '../components/VoiceLauncher';
import { FollowupSync } from '../components/FollowupSync';
import { StoreProvider } from '../lib/store';
import { useTheme } from '../lib/theme';

const icon = (emoji: string) =>
  function TabIcon({ focused }: { focused: boolean }) {
    return <Text style={{ fontSize: 20, opacity: focused ? 1 : 0.5 }}>{emoji}</Text>;
  };

/**
 * App-wide safety net: Expo Router uses a route's exported `ErrorBoundary` to catch render errors in
 * that route's subtree. Exported from the root layout, it catches an error from any screen so the app
 * shows a friendly "다시 시도" instead of a white crash. Uses plain colors (no app context) so it keeps
 * working even if the failure is in the theme/store. The user's data is untouched.
 */
export function ErrorBoundary({ error, retry }: ErrorBoundaryProps) {
  const dark = useColorScheme() === 'dark';
  const bg = dark ? '#111214' : '#ffffff';
  const fg = dark ? '#f2f2f2' : '#111111';
  const sub = dark ? '#9aa0a6' : '#666666';
  return (
    <View style={{ flex: 1, backgroundColor: bg, alignItems: 'center', justifyContent: 'center', padding: 28, gap: 14 }}>
      <Text style={{ fontSize: 34 }}>😵‍💫</Text>
      <Text style={{ color: fg, fontSize: 18, fontWeight: '800', textAlign: 'center' }}>이 화면에서 문제가 생겼어요</Text>
      <Text style={{ color: sub, fontSize: 13, textAlign: 'center', lineHeight: 19 }}>
        잠깐 멈췄어요. 아래 버튼으로 다시 시도하거나, 다른 탭으로 갔다가 돌아와 주세요. 저장된 데이터(기억·할 일·대화)는 안전해요.
      </Text>
      <Pressable onPress={retry} style={{ backgroundColor: '#6C8EF5', borderRadius: 12, paddingHorizontal: 24, paddingVertical: 12 }}>
        <Text style={{ color: '#ffffff', fontWeight: '800' }}>다시 시도</Text>
      </Pressable>
      <Text style={{ color: sub, fontSize: 11, textAlign: 'center' }} numberOfLines={3}>
        {String(error?.message || error || '알 수 없는 오류')}
      </Text>
    </View>
  );
}

export default function RootLayout() {
  const theme = useTheme();
  useEffect(() => {
    ensureProactiveRegistered();
    loadEleven(); // ElevenLabs 고급 목소리 키를 캐시에 올려 둔다(설정돼 있으면 speak이 바로 씀)
  }, []);
  return (
    <SafeAreaProvider>
      {/* Android draws edge-to-edge, so the keyboard is tracked natively instead of by window resizing. */}
      <KeyboardProvider>
        <StoreProvider>
          <StatusBar style="auto" />
          <VoiceLauncher />
          <NotificationRouter />
          <FollowupSync />
          <Tabs
            screenOptions={{
              headerStyle: { backgroundColor: theme.bg },
              headerTitleStyle: { color: theme.text, fontWeight: '800', fontSize: 18, letterSpacing: -0.3 },
              headerShadowVisible: false,
              tabBarStyle: { backgroundColor: theme.card, borderTopColor: theme.border, borderTopWidth: StyleSheet.hairlineWidth, height: 62, paddingTop: 6, paddingBottom: 8 },
              tabBarLabelStyle: { fontSize: 11, fontWeight: '700', letterSpacing: -0.2 },
              tabBarActiveTintColor: theme.accent,
              tabBarInactiveTintColor: theme.subtext,
              // Hide the bottom tab bar while typing, so the keyboard doesn't cover the chat input.
              tabBarHideOnKeyboard: true,
              sceneStyle: { backgroundColor: theme.bg },
            }}
          >
            {/* 홈은 '대화'. index는 대화로 보내는 리다이렉트라 탭에 안 보인다. */}
            <Tabs.Screen name="index" options={{ href: null, headerShown: false }} />
            {/* 1단계 재구성: 탭 4개 — 대화(홈) · 회사 · 할일·기억 · 설정 */}
            <Tabs.Screen
              name="chat"
              options={{
                title: '대화',
                tabBarIcon: icon('💬'),
                // 왼쪽 🏠 버튼으로 '오늘' 대시보드를 연다(탭에서 뺀 대신).
                headerLeft: () => (
                  <Pressable onPress={() => router.push('/today')} hitSlop={10} style={{ paddingLeft: 16, paddingRight: 8 }}>
                    <Text style={{ fontSize: 20 }}>🏠</Text>
                  </Pressable>
                ),
              }}
            />
            <Tabs.Screen name="company" options={{ title: '회사', tabBarIcon: icon('🏢') }} />
            <Tabs.Screen name="work" options={{ title: '할 일·기억', tabBarIcon: icon('✅') }} />
            <Tabs.Screen name="settings" options={{ title: '설정', tabBarIcon: icon('⚙️') }} />
            {/* 아래는 탭에 안 뜨는 화면들(href: null) — 다른 화면에서 열려 들어간다. */}
            <Tabs.Screen name="today" options={{ href: null, title: '오늘' }} />
            <Tabs.Screen name="journal" options={{ href: null, title: '여행 일지' }} />
            <Tabs.Screen name="tasks" options={{ href: null, title: '할 일' }} />
            <Tabs.Screen name="memory" options={{ href: null, title: '기억' }} />
            {/* Full-screen camera mode: not a tab (href: null), no header, no tab bar. */}
            <Tabs.Screen name="camera" options={{ href: null, headerShown: false, tabBarStyle: { display: 'none' } }} />
            {/* Shipment list drill-down: not a tab, has a header (title set per group). */}
            <Tabs.Screen name="shipments" options={{ href: null, title: '화물 목록' }} />
            <Tabs.Screen name="shipment" options={{ href: null, title: '화물 상세' }} />
            <Tabs.Screen name="finance" options={{ href: null, title: '자금 · 손익' }} />
            <Tabs.Screen name="account" options={{ href: null, title: '계좌 상세' }} />
            <Tabs.Screen name="schedule" options={{ href: null, title: '출항 스케줄' }} />
            <Tabs.Screen name="expenses" options={{ href: null, title: '내 지출 기록' }} />
            <Tabs.Screen name="documents" options={{ href: null, title: '내 견적·인보이스' }} />
            <Tabs.Screen name="feedback" options={{ href: null, title: '건의 · 버그 신고' }} />
          </Tabs>
        </StoreProvider>
      </KeyboardProvider>
    </SafeAreaProvider>
  );
}
