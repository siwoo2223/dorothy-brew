import { Fragment } from 'react';
import { Text, View } from 'react-native';

import { Chart, parseChartSpec } from './Chart';
import { ImageBlock, parseImageSpec } from './ImageBlock';
import { Markdown } from './Markdown';
import { useTheme } from '../lib/theme';

/**
 * Renders one of Dorothy's replies: Markdown text with any ```chart``` or ```image``` blocks turned
 * into a drawn chart / photo gallery. While a reply is still streaming, an unfinished such block is
 * hidden behind a small placeholder instead of flashing raw JSON.
 */
export function RichMessage({ text, color, streaming }: { text: string; color: string; streaming?: boolean }) {
  const t = useTheme();

  // A chart/image block still being written (open fence, no close) — don't show its JSON.
  let body = text;
  let pending = '';
  if (streaming) {
    for (const [tag, label] of [['```chart', '📊 그래프 그리는 중…'], ['```image', '🖼️ 사진 불러오는 중…']] as const) {
      const open = body.lastIndexOf(tag);
      if (open !== -1 && body.indexOf('```', open + tag.length) === -1) {
        body = body.slice(0, open);
        pending = label;
        break;
      }
    }
  }

  const parts: React.ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const m of body.matchAll(/```(chart|image)\s*\n([\s\S]*?)```/g)) {
    const idx = m.index ?? 0;
    const before = body.slice(last, idx).trim();
    if (before) parts.push(<Markdown key={`t${i}`} text={before} color={color} />);
    if (m[1] === 'image') {
      const img = parseImageSpec(m[2]);
      if (img) parts.push(<View key={`i${i}`} style={{ marginVertical: 4 }}><ImageBlock urls={img.urls} caption={img.caption} /></View>);
    } else {
      const spec = parseChartSpec(m[2]);
      if (spec) parts.push(<View key={`c${i}`} style={{ marginVertical: 4 }}><Chart spec={spec} /></View>);
      else parts.push(<Markdown key={`r${i}`} text={m[2]} color={color} />);
    }
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
      {pending ? <Text style={{ color: t.subtext, fontSize: 13 }}>{pending}</Text> : null}
    </View>
  );
}
