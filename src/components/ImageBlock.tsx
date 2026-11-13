import { useState } from 'react';
import { Image, Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { useTheme } from '../lib/theme';

/** Parses the JSON inside a ```image block; returns the list of URLs (+ optional caption). */
export function parseImageSpec(raw: string): { urls: string[]; caption?: string } | null {
  try {
    const o = JSON.parse(raw);
    const urls = Array.isArray(o.urls) ? o.urls.filter((u: unknown) => typeof u === 'string' && /^https?:\/\//.test(u)) : [];
    if (urls.length === 0) return null;
    return { urls, caption: o.caption ? String(o.caption) : undefined };
  } catch {
    return null;
  }
}

/** Shows company photos inline (thumbnail row); tapping one opens it full-screen. */
export function ImageBlock({ urls, caption }: { urls: string[]; caption?: string }) {
  const t = useTheme();
  const [open, setOpen] = useState<string | null>(null);
  return (
    <View style={{ gap: 6 }}>
      {caption ? <Text style={{ color: t.subtext, fontSize: 13 }}>🖼️ {caption}</Text> : null}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
        {urls.map((u, i) => (
          <Pressable key={i} onPress={() => setOpen(u)}>
            <Image source={{ uri: u }} style={[styles.thumb, { borderColor: t.border }]} />
          </Pressable>
        ))}
      </ScrollView>
      <Modal visible={!!open} transparent animationType="fade" onRequestClose={() => setOpen(null)}>
        <Pressable style={styles.backdrop} onPress={() => setOpen(null)}>
          {open ? <Image source={{ uri: open }} style={styles.full} resizeMode="contain" /> : null}
          <Text style={styles.closeHint}>탭하면 닫혀요</Text>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  thumb: { width: 120, height: 120, borderRadius: 10, borderWidth: StyleSheet.hairlineWidth, backgroundColor: 'rgba(127,127,127,0.12)' },
  backdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.92)', alignItems: 'center', justifyContent: 'center', padding: 12 },
  full: { width: '100%', height: '82%' },
  closeHint: { color: 'rgba(255,255,255,0.6)', fontSize: 13, marginTop: 14 },
});
