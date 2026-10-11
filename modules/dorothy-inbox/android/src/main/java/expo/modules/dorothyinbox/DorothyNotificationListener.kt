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
  }
}
