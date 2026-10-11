package expo.modules.dorothyinbox

import android.Manifest
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.CallLog
import android.provider.Settings
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

class DorothyInboxModule : Module() {
  private val context get() = requireNotNull(appContext.reactContext) { "React context is not ready" }

  override fun definition() = ModuleDefinition {
    Name("DorothyInbox")

    Function("isNotificationAccessGranted") {
      val enabled = Settings.Secure.getString(context.contentResolver, "enabled_notification_listeners").orEmpty()
      val me = ComponentName(context, DorothyNotificationListener::class.java)
      enabled.split(":").any { ComponentName.unflattenFromString(it) == me }
    }

    Function("openNotificationAccessSettings") {
      context.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    // Sideloaded apps on Android 13+ must first be allowed "restricted settings" from their App info page.
    Function("openAppDetails") {
      context.startActivity(
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
          .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      )
    }

    AsyncFunction("getMessages") { sinceMs: Double, limit: Int ->
      InboxStore.read(context, sinceMs.toLong(), limit)
    }

    Function("messageCount") { InboxStore.count(context) }

    Function("clearMessages") { InboxStore.clear(context) }

    AsyncFunction("getCalls") { sinceMs: Double, limit: Int ->
      if (context.checkSelfPermission(Manifest.permission.READ_CALL_LOG) != PackageManager.PERMISSION_GRANTED) {
        throw SecurityException("통화 기록 권한이 없어요.")
      }
      val calls = mutableListOf<Map<String, Any>>()
      context.contentResolver.query(
        CallLog.Calls.CONTENT_URI,
        arrayOf(CallLog.Calls.NUMBER, CallLog.Calls.CACHED_NAME, CallLog.Calls.TYPE, CallLog.Calls.DATE, CallLog.Calls.DURATION),
        "${CallLog.Calls.DATE} >= ?",
        arrayOf(sinceMs.toLong().toString()),
        "${CallLog.Calls.DATE} DESC",
      )?.use { c ->
        while (c.moveToNext() && calls.size < limit) {
          calls += mapOf(
            "number" to (c.getString(0) ?: ""),
            "name" to (c.getString(1) ?: ""),
            "type" to when (c.getInt(2)) {
              CallLog.Calls.INCOMING_TYPE -> "incoming"
              CallLog.Calls.OUTGOING_TYPE -> "outgoing"
              CallLog.Calls.MISSED_TYPE -> "missed"
              CallLog.Calls.REJECTED_TYPE -> "rejected"
              else -> "other"
            },
            "time" to c.getLong(3).toDouble(),
            "durationSec" to c.getLong(4).toDouble(),
          )
        }
      }
      calls
    }
  }
}
