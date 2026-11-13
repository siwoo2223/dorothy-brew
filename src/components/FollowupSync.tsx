import { useEffect, useRef } from 'react';
import { AppState, Platform } from 'react-native';

import { findHandledFollowups } from '../lib/followupSync';
import { useStore } from '../lib/store';

/**
 * Quietly clears "회신 대기" items the phone can tell are already handled — a KakaoTalk/messenger
 * reply the accessibility watcher saw, or an outgoing call-back. Runs on launch and whenever the app
 * returns to the front (right after the owner was in KakaoTalk). Does nothing on web / without grants.
 */
export function FollowupSync() {
  const { loaded, getState, actions } = useStore();
  const running = useRef(false);

  useEffect(() => {
    if (Platform.OS !== 'android' || !loaded) return;

    const run = async () => {
      if (running.current) return;
      running.current = true;
      try {
        const open = (getState().followups ?? []).filter((f) => !f.done);
        if (open.length === 0) return;
        const handled = await findHandledFollowups(open);
        for (const h of handled) actions.completeFollowup(h.id);
      } catch {
        // best-effort; never crash the app over reconciliation
      } finally {
        running.current = false;
      }
    };

    run();
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') run();
    });
    return () => sub.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded]);

  return null;
}
