package expo.modules.dorothyinbox

import android.app.Notification
import android.app.PendingIntent
import android.app.RemoteInput
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.service.notification.StatusBarNotification
import android.util.Log

/**
 * Lets Dorothy send a reply straight into a messenger chat (KakaoTalk, LINE, …) by reusing the
 * notification's own "quick reply" action. Android allows this: a notification can carry a reply
 * Action with a RemoteInput + PendingIntent, and firing it with the typed text is exactly what the
 * phone's own "빠른 답장" does. So this ONLY works for a room that has a live notification (the other
 * side messaged recently and the notification is still there) — it cannot start a brand-new chat.
 *
 * Nothing is stored on disk; the repliable actions live only in memory while notification access is on.
 */
object NotificationReplier {
  private data class Repliable(
    val pkg: String,
    val app: String,
    val room: String,
    val intent: PendingIntent,
    val inputs: Array<RemoteInput>,
    val time: Long,
  )

  // Keyed by room name; newest wins. Capped so it can't grow without bound.
  private val actions = LinkedHashMap<String, Repliable>()
  private const val MAX = 60

  /** Pulls the reply action out of a messenger notification and remembers it, keyed by the room name. */
  @Synchronized
  fun register(context: Context, sbn: StatusBarNotification) {
    try {
      if (sbn.packageName == context.packageName) return
      val n = sbn.notification ?: return
      if (n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
      val extras = n.extras ?: return
      val isChat = n.category == Notification.CATEGORY_MESSAGE || extras.containsKey(Notification.EXTRA_MESSAGES)
      val app = InboxStore.APPS[sbn.packageName] ?: (if (isChat) sbn.packageName else null) ?: return
      if (!isChat && !InboxStore.APPS.containsKey(sbn.packageName)) return

      val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()?.trim().orEmpty()
      val conversation = (extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)
        ?: extras.getCharSequence(Notification.EXTRA_SUB_TEXT))?.toString()?.trim().orEmpty()
      val room = conversation.ifEmpty { title }
      if (room.isEmpty()) return

      val reply = findReplyAction(n) ?: return
      val inputs = reply.remoteInputs?.filter { it.allowFreeFormInput }?.toTypedArray() ?: return
      if (inputs.isEmpty()) return
      val pi = reply.actionIntent ?: return

      actions.remove(room) // keep newest at the end
      actions[room] = Repliable(sbn.packageName, app, room, pi, inputs, sbn.postTime)
      while (actions.size > MAX) actions.remove(actions.keys.first())
    } catch (t: Throwable) {
      Log.w("DorothyInbox", "register reply", t)
    }
  }

  /** The action that carries a free-form reply box (prefer one tagged as REPLY). */
  private fun findReplyAction(n: Notification): Notification.Action? {
    val all = n.actions?.toList().orEmpty()
    val withInput = all.filter { a -> a.remoteInputs?.any { it.allowFreeFormInput } == true }
    if (withInput.isEmpty()) return null
    if (Build.VERSION.SDK_INT >= 28) {
      withInput.firstOrNull { it.semanticAction == Notification.Action.SEMANTIC_ACTION_REPLY }?.let { return it }
    }
    return withInput.first()
  }

  /** Room names Dorothy can reply to right now (a live notification, newer than maxAgeMs). */
  @Synchronized
  fun repliableRooms(maxAgeMs: Long): List<String> {
    val cutoff = System.currentTimeMillis() - maxAgeMs
    return actions.values.filter { it.time >= cutoff }.sortedByDescending { it.time }.map { it.room }
  }

  /** Sends `text` into the room's chat via its quick-reply action. Returns a short result map. */
  @Synchronized
  fun reply(context: Context, roomQuery: String, text: String): Map<String, Any> {
    if (text.isBlank()) return mapOf("ok" to false, "error" to "보낼 내용이 비어 있어요.")
    val q = roomQuery.trim()
    val match = actions.values
      .filter { it.room.contains(q, ignoreCase = true) || q.contains(it.room, ignoreCase = true) }
      .maxByOrNull { it.time }
      ?: return mapOf("ok" to false, "error" to "‘$q’ 방에 지금 바로 답장할 수 있는 알림이 없어요. 상대가 최근에 보낸 메시지 알림이 있어야 해요.")
    return try {
      val intent = Intent()
      val results = Bundle()
      for (ri in match.inputs) results.putCharSequence(ri.resultKey, text)
      RemoteInput.addResultsToIntent(match.inputs, intent, results)
      // KakaoTalk reads the reply text as a "typed" input (API 28+).
      if (Build.VERSION.SDK_INT >= 28) RemoteInput.setResultsSource(intent, RemoteInput.SOURCE_FREE_FORM_INPUT)
      match.intent.send(context, 0, intent)
      mapOf("ok" to true, "room" to match.room, "app" to match.app)
    } catch (t: Throwable) {
      Log.w("DorothyInbox", "send reply", t)
      mapOf("ok" to false, "error" to "전송에 실패했어요(알림이 사라졌거나 앱이 막았을 수 있어요).")
    }
  }
}
