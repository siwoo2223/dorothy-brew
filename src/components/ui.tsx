import { ReactNode } from 'react';
import { Platform, Pressable, StyleSheet, Text, TextInput, TextInputProps, View, ViewStyle } from 'react-native';

import { useTheme } from '../lib/theme';

export function Card({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  const t = useTheme();
  return <View style={[styles.card, cardShadow, { backgroundColor: t.card, borderColor: t.border }, style]}>{children}</View>;
}

export function Title({ children }: { children: ReactNode }) {
  const t = useTheme();
  return <Text style={[styles.title, { color: t.text }]}>{children}</Text>;
}

export function Body({ children, muted }: { children: ReactNode; muted?: boolean }) {
  const t = useTheme();
  return <Text style={[styles.body, { color: muted ? t.subtext : t.text }]}>{children}</Text>;
}

export function Button({
  label,
  onPress,
  disabled,
  variant = 'primary',
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  variant?: 'primary' | 'ghost' | 'danger';
}) {
  const t = useTheme();
  const bg = variant === 'primary' ? t.accent : variant === 'danger' ? t.dangerSoft : t.accentSoft;
  const fg = variant === 'primary' ? t.accentText : variant === 'danger' ? t.danger : t.accent;
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        styles.button,
        variant === 'primary' && buttonShadow,
        { backgroundColor: bg, opacity: disabled ? 0.45 : pressed ? 0.82 : 1, transform: [{ scale: pressed ? 0.985 : 1 }] },
      ]}
    >
      <Text style={[styles.buttonText, { color: fg }]}>{label}</Text>
    </Pressable>
  );
}

export function Input(props: TextInputProps) {
  const t = useTheme();
  return (
    <TextInput
      placeholderTextColor={t.subtext}
      {...props}
      style={[styles.input, { color: t.text, backgroundColor: t.bg, borderColor: t.border }, props.style]}
    />
  );
}

// Soft, warm shadows — light on Android (elevation) so dark cards don't halo.
const cardShadow = Platform.select({
  ios: { shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 10, shadowOffset: { width: 0, height: 3 } },
  android: { elevation: 1 },
  default: {},
}) as ViewStyle;

const buttonShadow = Platform.select({
  ios: { shadowColor: '#000', shadowOpacity: 0.12, shadowRadius: 6, shadowOffset: { width: 0, height: 2 } },
  android: { elevation: 2 },
  default: {},
}) as ViewStyle;

const styles = StyleSheet.create({
  card: { borderRadius: 20, borderWidth: StyleSheet.hairlineWidth, padding: 18, gap: 10 },
  title: { fontSize: 17, fontWeight: '800', letterSpacing: -0.2 },
  body: { fontSize: 15, lineHeight: 22 },
  button: { borderRadius: 16, paddingVertical: 13, paddingHorizontal: 18, alignItems: 'center' },
  buttonText: { fontSize: 15, fontWeight: '700', letterSpacing: -0.1 },
  input: { borderRadius: 14, borderWidth: 1, paddingHorizontal: 15, paddingVertical: 13, fontSize: 15 },
});
