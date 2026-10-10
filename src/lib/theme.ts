import { useColorScheme } from 'react-native';

const light = {
  bg: '#F6F5FB',
  card: '#FFFFFF',
  text: '#1D1B2C',
  subtext: '#6B6880',
  border: '#E4E1F0',
  accent: '#6C5CE7',
  accentText: '#FFFFFF',
  userBubble: '#6C5CE7',
  assistantBubble: '#FFFFFF',
  danger: '#D64545',
  warn: '#E08A00',
};

const dark: typeof light = {
  bg: '#121119',
  card: '#1D1B29',
  text: '#F1EFFA',
  subtext: '#A29FB8',
  border: '#2E2B40',
  accent: '#8C7FF0',
  accentText: '#FFFFFF',
  userBubble: '#6C5CE7',
  assistantBubble: '#1D1B29',
  danger: '#F06A6A',
  warn: '#F2A93B',
};

export type Theme = typeof light;

export function useTheme(): Theme {
  return useColorScheme() === 'dark' ? dark : light;
}
