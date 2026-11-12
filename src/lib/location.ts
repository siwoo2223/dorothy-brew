import * as Location from 'expo-location';
import { Platform } from 'react-native';

/**
 * The phone's current location, used only to ground "오늘 날씨"/이동 안내 in where the owner actually is
 * right now (instead of a home address from memory). Read on demand; never stored or sent anywhere on its own.
 */
export type Place = { label: string; city?: string; region?: string; latitude: number; longitude: number };

export async function locationGranted(): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  const { status } = await Location.getForegroundPermissionsAsync();
  return status === 'granted';
}

export async function requestLocation(): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  const { status } = await Location.requestForegroundPermissionsAsync();
  return status === 'granted';
}

/** Current place as a short "시 구" label with coordinates, or null when not permitted/unavailable. */
export async function currentPlace(): Promise<Place | null> {
  if (!(await locationGranted())) return null;
  try {
    const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
    const { latitude, longitude } = pos.coords;
    let city: string | undefined;
    let region: string | undefined;
    try {
      const [g] = await Location.reverseGeocodeAsync({ latitude, longitude });
      if (g) {
        city = g.city ?? g.subregion ?? undefined;
        region = g.region ?? undefined;
      }
    } catch {
      // Reverse geocoding can fail offline; coordinates alone still work for weather.
    }
    const label = [region, city].filter(Boolean).join(' ') || `${latitude.toFixed(3)}, ${longitude.toFixed(3)}`;
    return { label, city, region, latitude, longitude };
  } catch {
    return null;
  }
}
