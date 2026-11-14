import { useColorScheme } from 'react-native';

/**
 * Dorothy's design tokens. 2026-11 개편: 보라/라벤더 → 따뜻한 크림 + 테라코타 포인트로 전면 교체해
 * Claude 앱 같은 차분하고 아기자기한 느낌을 준다. accentSoft/dangerSoft 는 버튼·칩의 은은한 배경.
 */
const light = {
  bg: '#FAF8F4', // warm off-white
  card: '#FFFFFF',
  text: '#28241F', // warm near-black
  subtext: '#8C837A',
  border: '#ECE6DD',
  accent: '#BE5D3E', // terracotta
  accentText: '#FFFFFF',
  accentSoft: '#F5E9E2', // soft terracotta tint (ghost buttons / chips)
  userBubble: '#BE5D3E',
  assistantBubble: '#FFFFFF',
  danger: '#C0453B',
  dangerSoft: '#FAE8E6',
  warn: '#C2892F',
};

const dark: typeof light = {
  bg: '#1A1714', // warm charcoal
  card: '#25211C',
  text: '#F1EBE2',
  subtext: '#A79E93',
  border: '#36302A',
  accent: '#E0855F', // lighter terracotta for dark
  accentText: '#1A1714',
  accentSoft: '#332A23',
  userBubble: '#BE5D3E',
  assistantBubble: '#25211C',
  danger: '#E8776B',
  dangerSoft: '#3A241F',
  warn: '#D7A24A',
};

export type Theme = typeof light;

export function useTheme(): Theme {
  return useColorScheme() === 'dark' ? dark : light;
}
