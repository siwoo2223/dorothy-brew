package expo.modules.dorothyinbox

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log

/** Runs whenever a notification is posted, even while the app is closed, once notification access is granted. */
class DorothyNotificationListener : NotificationListenerService() {
  override fun onNotificationPosted(sbn: StatusBarNotification) {
    try {
      InboxStore.capture(applicationContext, sbn)
    } catch (e: Throwable) {
      Log.w("DorothyInbox", "Could not save notification", e)
    }
    // Remember the chat's quick-reply action so Dorothy can reply straight into the room.
    try {
      NotificationReplier.register(applicationContext, sbn)
    } catch (e: Throwable) {
      Log.w("DorothyInbox", "Could not register reply action", e)
    }
  }

  // On (re)connect, pick up reply actions from notifications already on screen, so replying works
  // right after the app/phone starts without waiting for a new message.
  override fun onListenerConnected() {
    try {
      activeNotifications?.forEach { NotificationReplier.register(applicationContext, it) }
    } catch (e: Throwable) {
      Log.w("DorothyInbox", "scan active notifications", e)
    }
  }
}
