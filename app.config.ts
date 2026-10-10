import type { ConfigContext, ExpoConfig } from 'expo/config';

// Google sends the OAuth result back to the reversed client ID scheme
// (com.googleusercontent.apps.<id>), so the app must register it alongside its own scheme.
const reversed = (clientId?: string) => (clientId ? clientId.split('.').reverse().join('.') : null);

export default ({ config }: ConfigContext): ExpoConfig => {
  const schemes = [
    'dorothy',
    reversed(process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID),
    reversed(process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID),
  ].filter((s): s is string => !!s);

  return {
    ...config,
    name: config.name ?? '도로시',
    slug: config.slug ?? 'dorothy',
    scheme: [...new Set(schemes)],
  };
};
