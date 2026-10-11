import { useEffect, useRef, useState } from 'react';
import { Keyboard, Platform, View } from 'react-native';

/**
 * How much of the attached view the on-screen keyboard covers, to use as bottom padding.
 *
 * Android apps now draw edge-to-edge, so the window no longer shrinks when the keyboard opens and
 * KeyboardAvoidingView needs a hand-tuned offset (header, tab bar, fold state). Measuring the
 * view against the keyboard's top edge works on every screen without guessing.
 */
export function useKeyboardInset() {
  const ref = useRef<View>(null);
  const [inset, setInset] = useState(0);

  useEffect(() => {
    const ios = Platform.OS === 'ios';
    const show = Keyboard.addListener(ios ? 'keyboardWillShow' : 'keyboardDidShow', (e) => {
      const keyboardTop = e.endCoordinates.screenY;
      ref.current?.measureInWindow((_x, y, _w, height) => {
        // Fall back to the keyboard's height if the measurement is unusable.
        const covered = height > 0 ? y + height - keyboardTop : e.endCoordinates.height;
        setInset(Math.max(0, Math.round(covered)));
      });
    });
    const hide = Keyboard.addListener(ios ? 'keyboardWillHide' : 'keyboardDidHide', () => setInset(0));
    return () => {
      show.remove();
      hide.remove();
    };
  }, []);

  return { ref, inset };
}
