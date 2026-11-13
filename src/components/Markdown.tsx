import { Linking, StyleSheet, Text, View } from 'react-native';

import { useTheme } from '../lib/theme';

/**
 * A small, dependency-free Markdown renderer for Dorothy's replies, so answers read like the Claude
 * app: bold, bullet and numbered lists, headings, inline code, and links — not a wall of plain text.
 * It covers the formatting Claude actually emits; anything it doesn't recognise falls through as text.
 */

type Seg = { text: string; bold?: boolean; code?: boolean; url?: string };

/** Splits one line into styled spans (**bold**, `code`, [label](url)). */
function parseInline(s: string): Seg[] {
  const segs: Seg[] = [];
  const re = /(\*\*([^*]+)\*\*)|(`([^`]+)`)|(\[([^\]]+)\]\(([^)]+)\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s))) {
    if (m.index > last) segs.push({ text: s.slice(last, m.index) });
    if (m[2] != null) segs.push({ text: m[2], bold: true });
    else if (m[4] != null) segs.push({ text: m[4], code: true });
    else if (m[6] != null) segs.push({ text: m[6], url: m[7] });
    last = re.lastIndex;
  }
  if (last < s.length) segs.push({ text: s.slice(last) });
  return segs.length ? segs : [{ text: s }];
}

function Inline({ segs, color, size }: { segs: Seg[]; color: string; size: number }) {
  const t = useTheme();
  return (
    <>
      {segs.map((seg, i) => {
        if (seg.url) {
          return (
            <Text key={i} style={{ color: t.accent, textDecorationLine: 'underline' }} onPress={() => Linking.openURL(seg.url!).catch(() => {})}>
              {seg.text}
            </Text>
          );
        }
        if (seg.code) {
          return (
            <Text key={i} style={{ color, fontFamily: 'monospace', fontSize: size - 1, backgroundColor: 'rgba(127,127,127,0.18)' }}>
              {seg.text}
            </Text>
          );
        }
        return (
          <Text key={i} style={{ color, fontWeight: seg.bold ? '700' : '400' }}>
            {seg.text}
          </Text>
        );
      })}
    </>
  );
}

export function Markdown({ text, color, size = 15 }: { text: string; color: string; size?: number }) {
  // Strip code fences' backticks but keep the inner lines (we render them as plain monospace blocks).
  const lines = text.replace(/\r/g, '').split('\n');
  const blocks: React.ReactNode[] = [];

  lines.forEach((raw, idx) => {
    const line = raw.replace(/\s+$/, '');
    if (line.trim() === '') {
      blocks.push(<View key={idx} style={{ height: 6 }} />);
      return;
    }
    // ``` fence lines — skip the marker itself.
    if (/^```/.test(line.trim())) return;

    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      blocks.push(
        <Text key={idx} style={{ color, fontWeight: '800', fontSize: size + (level === 1 ? 4 : level === 2 ? 2 : 1), marginTop: idx ? 4 : 0, lineHeight: (size + 6) * 1.3 }}>
          <Inline segs={parseInline(heading[2])} color={color} size={size + 2} />
        </Text>,
      );
      return;
    }

    const bullet = line.match(/^\s*[-•*]\s+(.*)$/);
    if (bullet) {
      blocks.push(
        <View key={idx} style={styles.li}>
          <Text style={{ color, fontSize: size, lineHeight: size * 1.5 }}>•</Text>
          <Text style={{ color, fontSize: size, lineHeight: size * 1.5, flex: 1 }}>
            <Inline segs={parseInline(bullet[1])} color={color} size={size} />
          </Text>
        </View>,
      );
      return;
    }

    const numbered = line.match(/^\s*(\d+)\.\s+(.*)$/);
    if (numbered) {
      blocks.push(
        <View key={idx} style={styles.li}>
          <Text style={{ color, fontSize: size, lineHeight: size * 1.5, fontWeight: '600' }}>{numbered[1]}.</Text>
          <Text style={{ color, fontSize: size, lineHeight: size * 1.5, flex: 1 }}>
            <Inline segs={parseInline(numbered[2])} color={color} size={size} />
          </Text>
        </View>,
      );
      return;
    }

    blocks.push(
      <Text key={idx} style={{ color, fontSize: size, lineHeight: size * 1.5 }}>
        <Inline segs={parseInline(line)} color={color} size={size} />
      </Text>,
    );
  });

  return <View>{blocks}</View>;
}

const styles = StyleSheet.create({
  li: { flexDirection: 'row', gap: 7, alignItems: 'flex-start' },
});
