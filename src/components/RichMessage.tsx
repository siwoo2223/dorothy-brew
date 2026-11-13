import { Fragment } from 'react';
import { Text, View } from 'react-native';

import { Chart, parseChartSpec } from './Chart';
import { Markdown } from './Markdown';
import { useTheme } from '../lib/theme';

/**
 * Renders one of Dorothy's replies: Markdown text with any ```chart``` blocks turned into a drawn
 * chart. While a reply is still streaming, an unfinished chart block is hidden behind a small
 * placeholder instead of flashing raw JSON.
 */
export function RichMessage({ text, color, streaming }: { text: string; color: string; streaming?: boolean }) {
  const t = useTheme();

  // A chart block still being written (open ```chart with no closing fence) — don't show its JSON.
  let body = text;
  let pending = false;
  if (streaming) {
    const open = body.lastIndexOf('```chart');
    if (open !== -1 && body.indexOf('```', open + 8) === -1) {
      body = body.slice(0, open);
      pending = true;
    }
  }

  const parts: React.ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const m of body.matchAll(/```chart\s*\n([\s\S]*?)```/g)) {
    const idx = m.index ?? 0;
    const before = body.slice(last, idx).trim();
    if (before) parts.push(<Markdown key={`t${i}`} text={before} color={color} />);
    const spec = parseChartSpec(m[1]);
    if (spec) parts.push(<View key={`c${i}`} style={{ marginVertical: 4 }}><Chart spec={spec} /></View>);
    else parts.push(<Markdown key={`r${i}`} text={m[1]} color={color} />);
    last = idx + m[0].length;
    i++;
  }
  const rest = body.slice(last).trim();
  if (rest) parts.push(<Markdown key="tail" text={rest} color={color} />);
  if (parts.length === 0 && !pending) parts.push(<Markdown key="only" text={body} color={color} />);

  return (
    <View style={{ gap: 6 }}>
      {parts.map((p, k) => (
        <Fragment key={k}>{p}</Fragment>
      ))}
      {pending && <Text style={{ color: t.subtext, fontSize: 13 }}>📊 그래프 그리는 중…</Text>}
    </View>
  );
}
