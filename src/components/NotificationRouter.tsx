import { useLastNotificationResponse, DEFAULT_ACTION_IDENTIFIER } from 'expo-notifications';
import { router, useRootNavigationState, type Href } from 'expo-router';
import { useEffect, useRef } from 'react';

/** Opens the screen a tapped notification points to (its data.url), e.g. the nightly plan. */
export function NotificationRouter() {
  const response = useLastNotificationResponse();
  const navReady = !!useRootNavigationState()?.key;
  const handled = useRef<string | null>(null);

  useEffect(() => {
    if (!navReady || !response || response.actionIdentifier !== DEFAULT_ACTION_IDENTIFIER) return;
    const url = response.notification.request.content.data?.url;
    const id = `${response.notification.request.identifier}@${response.notification.date}`;
    if (typeof url !== 'string' || handled.current === id) return;
    handled.current = id;
    router.navigate(url as Href);
  }, [navReady, response]);

  return null;
}
