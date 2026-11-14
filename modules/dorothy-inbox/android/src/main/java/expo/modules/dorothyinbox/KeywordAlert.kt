package expo.modules.dorothyinbox

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

/**
 * Real-time keyword alerts. The notification listener already sees every incoming messenger message;
 * when a fresh one contains a watched keyword (e.g. "페소", "레이트", "삽니다"), Dorothy posts a local
 * notification at once — even while the app is closed — so the owner catches it live. Keywords are set
 * from the app (SharedPreferences). No message text leaves the phone.
 */
object KeywordAlert {
  private const val PREF = "dorothy_kw"
  private const val KEY = "keywords"
  private const val CHANNEL = "dorothy-keyword"
  private var idSeq = 7100

  fun setKeywords(context: Context, list: List<String>) {
    val clean = list.map { it.trim() }.filter { it.isNotEmpty() }.toSet()
    context.getSharedPreferences(PREF, Context.MODE_PRIVATE).edit().putStringSet(KEY, clean).apply()
  }

  fun getKeywords(context: Context): List<String> =
    context.getSharedPreferences(PREF, Context.MODE_PRIVATE).getStringSet(KEY, emptySet())?.toList() ?: emptyList()

  /** Posts an alert if the text contains any watched keyword. Safe to call on every captured message. */
  fun check(context: Context, conversation: String, sender: String, text: String, app: String) {
    try {
      val kws = getKeywords(context)
      if (kws.isEmpty() || text.isEmpty()) return
      val hit = kws.firstOrNull { text.contains(it, ignoreCase = true) } ?: return
      notify(context, hit, conversation.ifEmpty { app }, sender, text)
    } catch (t: Throwable) {
      Log.w("DorothyInbox", "keyword check", t)
    }
  }

  private fun ensureChannel(context: Context) {
    val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    if (nm.getNotificationChannel(CHANNEL) == null) {
      nm.createNotificationChannel(
        NotificationChannel(CHANNEL, "키워드 알림", NotificationManager.IMPORTANCE_HIGH).apply {
          description = "등록한 키워드가 메시지에 뜨면 바로 알려 줍니다."
        }
      )
    }
  }

  private fun notify(context: Context, kw: String, room: String, sender: String, text: String) {
    ensureChannel(context)
    val launch = context.packageManager.getLaunchIntentForPackage(context.packageName)
    val pi = launch?.let {
      PendingIntent.getActivity(context, 0, it, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }
    val body = (if (sender.isNotEmpty() && sender != room) "$sender: " else "") + text.take(160)
    val n = NotificationCompat.Builder(context, CHANNEL)
      .setSmallIcon(context.applicationInfo.icon)
      .setContentTitle("🔔 ‘$kw’ — $room")
      .setContentText(body)
      .setStyle(NotificationCompat.BigTextStyle().bigText(body))
      .setPriority(NotificationCompat.PRIORITY_HIGH)
      .setCategory(NotificationCompat.CATEGORY_MESSAGE)
      .setAutoCancel(true)
      .apply { if (pi != null) setContentIntent(pi) }
      .build()
    try {
      NotificationManagerCompat.from(context).notify(idSeq++, n)
    } catch (se: SecurityException) {
      // POST_NOTIFICATIONS not granted (Android 13+): the app asks for it in Settings.
      Log.w("DorothyInbox", "keyword notify blocked", se)
    }
  }
}
