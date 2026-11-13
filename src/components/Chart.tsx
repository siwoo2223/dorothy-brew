import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, Polyline, Line as SvgLine, Circle as Dot } from 'react-native-svg';

import { useTheme } from '../lib/theme';

/**
 * Renders a small chart from a spec Dorothy emits in a ```chart fenced block, so answers can show a
 * picture (status breakdown, monthly trend, share) instead of only numbers. Dependency: react-native-svg.
 *
 * Spec: { type: 'bar' | 'line' | 'pie', title?, unit?, data: [{ label, value }] }
 * Tolerant of bad input — returns null rather than crash the chat.
 */

export type ChartSpec = {
  type: 'bar' | 'line' | 'pie';
  title?: string;
  unit?: string;
  data: { label: string; value: number }[];
};

const PALETTE = ['#6C8EF5', '#4CC38A', '#F5A623', '#E5698C', '#9B7EDE', '#45B0C9', '#E57373', '#7FB77E'];

/** Parses the JSON inside a ```chart block; returns null if it isn't a usable chart. */
export function parseChartSpec(raw: string): ChartSpec | null {
  try {
    const o = JSON.parse(raw);
    if (!o || !Array.isArray(o.data)) return null;
    const data = o.data
      .map((d: { label?: unknown; value?: unknown }) => ({ label: String(d.label ?? ''), value: Number(d.value) }))
      .filter((d: { label: string; value: number }) => d.label !== '' && Number.isFinite(d.value));
    if (data.length === 0) return null;
    const type = o.type === 'line' || o.type === 'pie' ? o.type : 'bar';
    return { type, title: o.title ? String(o.title) : undefined, unit: o.unit ? String(o.unit) : undefined, data };
  } catch {
    return null;
  }
}

const fmt = (n: number) => {
  if (Math.abs(n) >= 1000) return n.toLocaleString('ko-KR');
  return String(Math.round(n * 100) / 100);
};

export function Chart({ spec }: { spec: ChartSpec }) {
  const t = useTheme();
  const [w, setW] = useState(0);
  const color = (i: number) => PALETTE[i % PALETTE.length];

  return (
    <View style={[styles.wrap, { borderColor: t.border, backgroundColor: t.card }]} onLayout={(e) => setW(e.nativeEvent.layout.width)}>
      {spec.title ? <Text style={[styles.title, { color: t.text }]}>{spec.title}{spec.unit ? ` (${spec.unit})` : ''}</Text> : null}
      {w > 0 && (spec.type === 'bar' ? <Bars spec={spec} color={color} t={t} /> : spec.type === 'line' ? <LineChart spec={spec} width={w - 24} t={t} /> : <Pie spec={spec} width={w - 24} color={color} t={t} />)}
    </View>
  );
}

type Theme = ReturnType<typeof useTheme>;

/** Horizontal bars — reads well with Korean labels, no axis juggling. */
function Bars({ spec, color, t }: { spec: ChartSpec; color: (i: number) => string; t: Theme }) {
  const max = Math.max(...spec.data.map((d) => d.value), 0) || 1;
  return (
    <View style={{ gap: 8 }}>
      {spec.data.map((d, i) => (
        <View key={i} style={{ gap: 3 }}>
          <View style={styles.barRow}>
            <Text style={{ color: t.subtext, fontSize: 12, flex: 1 }} numberOfLines={1}>{d.label}</Text>
            <Text style={{ color: t.text, fontSize: 12, fontWeight: '700' }}>{fmt(d.value)}</Text>
          </View>
          <View style={[styles.track, { backgroundColor: t.border }]}>
            <View style={{ width: `${Math.max(2, (d.value / max) * 100)}%`, height: '100%', borderRadius: 5, backgroundColor: color(i) }} />
          </View>
        </View>
      ))}
    </View>
  );
}

function LineChart({ spec, width, t }: { spec: ChartSpec; width: number; t: Theme }) {
  const h = 140;
  const padL = 8;
  const padB = 22;
  const padT = 8;
  const vals = spec.data.map((d) => d.value);
  const max = Math.max(...vals);
  const min = Math.min(...vals, 0);
  const span = max - min || 1;
  const n = spec.data.length;
  const x = (i: number) => padL + (n <= 1 ? (width - padL) / 2 : (i * (width - padL * 2)) / (n - 1));
  const y = (v: number) => padT + (1 - (v - min) / span) * (h - padT - padB);
  const pts = spec.data.map((d, i) => `${x(i)},${y(d.value)}`).join(' ');
  return (
    <View>
      <Svg width={width} height={h}>
        <SvgLine x1={padL} y1={h - padB} x2={width - padL} y2={h - padB} stroke={t.border} strokeWidth={1} />
        <Polyline points={pts} fill="none" stroke={t.accent} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
        {spec.data.map((d, i) => (
          <Dot key={i} cx={x(i)} cy={y(d.value)} r={3.5} fill={t.accent} />
        ))}
      </Svg>
      <View style={styles.lineLabels}>
        {spec.data.map((d, i) => (
          <Text key={i} style={{ color: t.subtext, fontSize: 10, flex: 1, textAlign: 'center' }} numberOfLines={1}>{d.label}</Text>
        ))}
      </View>
    </View>
  );
}

/** Donut via a stroked circle per slice (strokeDasharray), rotated so slices start at top. */
function Pie({ spec, width, color, t }: { spec: ChartSpec; width: number; color: (i: number) => string; t: Theme }) {
  const size = Math.min(width, 180);
  const r = size / 2 - 14;
  const cx = size / 2;
  const cy = size / 2;
  const C = 2 * Math.PI * r;
  const total = spec.data.reduce((s, d) => s + Math.max(0, d.value), 0) || 1;
  const fracs = spec.data.map((d) => Math.max(0, d.value) / total);
  // Cumulative offset before each slice (built without mutating across the render map).
  const offsets: number[] = [];
  let run = 0;
  for (const f of fracs) { offsets.push(run); run += f; }
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
      <Svg width={size} height={size}>
        {spec.data.map((d, i) => {
          const dash = fracs[i] * C;
          return (
            <Circle
              key={i}
              cx={cx}
              cy={cy}
              r={r}
              fill="none"
              stroke={color(i)}
              strokeWidth={14}
              strokeDasharray={`${dash} ${C - dash}`}
              strokeDashoffset={-offsets[i] * C}
              // Start at 12 o'clock.
              transform={`rotate(-90 ${cx} ${cy})`}
            />
          );
        })}
      </Svg>
      <View style={{ flex: 1, gap: 5 }}>
        {spec.data.map((d, i) => (
          <View key={i} style={styles.legendRow}>
            <View style={{ width: 10, height: 10, borderRadius: 3, backgroundColor: color(i) }} />
            <Text style={{ color: t.subtext, fontSize: 12, flex: 1 }} numberOfLines={1}>{d.label}</Text>
            <Text style={{ color: t.text, fontSize: 12, fontWeight: '700' }}>{Math.round((Math.max(0, d.value) / total) * 100)}%</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 14, padding: 12, gap: 10, marginTop: 4 },
  title: { fontSize: 14, fontWeight: '800' },
  barRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  track: { height: 10, borderRadius: 5, overflow: 'hidden' },
  lineLabels: { flexDirection: 'row', marginTop: 2 },
  legendRow: { flexDirection: 'row', alignItems: 'center', gap: 7 },
});
