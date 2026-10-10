import { Tabs } from 'expo-router/js-tabs';
import { StatusBar } from 'expo-status-bar';
import { Text } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import '../lib/notifications';
import { StoreProvider } from '../lib/store';
import { useTheme } from '../lib/theme';

const icon = (emoji: string) =>
  function TabIcon({ focused }: { focused: boolean }) {
    return <Text style={{ fontSize: 20, opacity: focused ? 1 : 0.5 }}>{emoji}</Text>;
  };

export default function RootLayout() {
  const theme = useTheme();
  return (
    <SafeAreaProvider>
      <StoreProvider>
        <StatusBar style="auto" />
        <Tabs
          screenOptions={{
            headerStyle: { backgroundColor: theme.bg },
            headerTitleStyle: { color: theme.text, fontWeight: '700' },
            headerShadowVisible: false,
            tabBarStyle: { backgroundColor: theme.card, borderTopColor: theme.border },
            tabBarActiveTintColor: theme.accent,
            tabBarInactiveTintColor: theme.subtext,
            sceneStyle: { backgroundColor: theme.bg },
          }}
        >
          <Tabs.Screen name="index" options={{ title: '오늘', tabBarIcon: icon('☀️') }} />
          <Tabs.Screen name="chat" options={{ title: '도로시', tabBarIcon: icon('💬') }} />
          <Tabs.Screen name="tasks" options={{ title: '할 일', tabBarIcon: icon('✅') }} />
          <Tabs.Screen name="memory" options={{ title: '기억', tabBarIcon: icon('🧠') }} />
          <Tabs.Screen name="settings" options={{ title: '설정', tabBarIcon: icon('⚙️') }} />
        </Tabs>
      </StoreProvider>
    </SafeAreaProvider>
  );
}
