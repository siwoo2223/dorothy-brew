import { router, useRootNavigationState } from 'expo-router';
import { useEffect, useRef } from 'react';
import { AppState } from 'react-native';

import { useStore } from '../lib/store';
import { voiceInputAvailable } from '../lib/voice';

/**
 * "열면 바로 듣기": whenever the app comes to the front — icon, Samsung side-button shortcut,
 * "하이 빅스비, 도로시 열어줘", or the Android assistant gesture — jump to chat and start listening.
 */
export function VoiceLauncher() {
  const { state, loaded } = useStore();
  const navReady = !!useRootNavigationState()?.key;
  const mode = state.voiceOnOpen;
  const launchedOnce = useRef(false);

  useEffect(() => {
    if (!loaded || !navReady || mode === 'off' || !voiceInputAvailable() || !state.apiKey) return;
    const go = () => router.navigate({ pathname: '/chat', params: { voice: mode === 'standby' ? 'standby' : '1' } });
    if (!launchedOnce.current) {
      launchedOnce.current = true;
      go();
    }
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') go();
    });
    return () => sub.remove();
  }, [loaded, navReady, mode, state.apiKey]);

  return null;
}
