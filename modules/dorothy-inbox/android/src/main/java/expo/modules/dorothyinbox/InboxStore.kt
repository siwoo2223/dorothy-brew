package expo.modules.dorothyinbox

import android.app.Notification
import android.content.Context
import android.os.Bundle
import android.service.notification.StatusBarNotification
import org.json.JSONObject
import java.io.File

/**
 * Messenger notifications saved as JSON lines in the app's private storage. Nothing leaves the
 * phone from here; the app reads these only when the user asks Dorothy to sum things up.
 */
object InboxStore {
  /**
   * Known messengers, with friendly names. Any other app is read too when its notification is a chat
   * message (CATEGORY_MESSAGE or MessagingStyle), so new messengers work without an update.
   */
  val APPS = mapOf(
    "com.kakao.talk" to "카카오톡",
    "com.facebook.orca" to "Messenger",
    "com.facebook.mlite" to "Messenger",
    "com.viber.voip" to "Viber",
    "com.whatsapp" to "WhatsApp",
    "com.whatsapp.w4b" to "WhatsApp Business",
    "org.telegram.messenger" to "Telegram",
    "jp.naver.line.android" to "LINE",
    "com.tencent.mm" to "WeChat",
    "com.instagram.android" to "Instagram",
    "com.discord" to "Discord",
    "com.Slack" to "Slack",
    "com.microsoft.teams" to "Teams",
    "com.samsung.android.messaging" to "문자",
    "com.google.android.apps.messaging" to "문자",
  )

  private const val FILE = "dorothy-inbox.jsonl"
  /** Chats imported from export files (e.g. KakaoTalk "대화 내보내기"); kept regardless of age. */
  private const val HISTORY = "dorothy-history.jsonl"
  private const val MAX_HISTORY_BYTES = 20L * 1024 * 1024
  private const val KEEP_MS = 14L * 24 * 60 * 60 * 1000
  private const val MAX_BYTES = 2L * 1024 * 1024

  // Verification codes are useless for planning and sensitive; never store them.
  private val OTP = Regex("(인증|verification|OTP|passcode|code|코드|일회용)", RegexOption.IGNORE_CASE)
  private val DIGITS = Regex("\\b\\d{4,8}\\b")

  // Apps re-post the same notification as new messages arrive; remember what was already saved.
  private val seen = LinkedHashSet<String>()

  private fun file(context: Context) = File(context.filesDir, FILE)

  @Synchronized
  fun capture(context: Context, sbn: StatusBarNotification) {
    if (sbn.packageName == context.packageName) return
    val n = sbn.notification
    if (n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
    val extras = n.extras ?: return
    val isChat = n.category == Notification.CATEGORY_MESSAGE || extras.containsKey(Notification.EXTRA_MESSAGES)
    val app = APPS[sbn.packageName] ?: (if (isChat) appLabel(context, sbn.packageName) else null) ?: return
    val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()?.trim().orEmpty()
    val conversation = (extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)
      ?: extras.getCharSequence(Notification.EXTRA_SUB_TEXT))?.toString()?.trim().orEmpty()

    val entries = mutableListOf<JSONObject>()
    @Suppress("DEPRECATION")
    val messages = extras.getParcelableArray(Notification.EXTRA_MESSAGES)
    if (!messages.isNullOrEmpty()) {
      for (m in messages) {
        val b = m as? Bundle ?: continue
        val text = b.getCharSequence("text")?.toString()?.trim().orEmpty()
        val sender = b.getCharSequence("sender")?.toString()?.trim().orEmpty().ifEmpty { title }
        val time = b.getLong("time").takeIf { it > 0 } ?: sbn.postTime
        entry(sbn.packageName, app, conversation.ifEmpty { title }, sender, text, time, exactTime = true)?.let(entries::add)
      }
    } else {
      val text = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
        ?: extras.getCharSequence(Notification.EXTRA_TEXT))?.toString()?.trim().orEmpty()
      entry(sbn.packageName, app, conversation.ifEmpty { title }, title, text, sbn.postTime, exactTime = false)?.let(entries::add)
    }
    if (entries.isEmpty()) return

    val f = file(context)
    if (seen.isEmpty()) loadRecentKeys(f)
    val fresh = entries.filter { seen.add(it.getString("key")) }
    if (fresh.isEmpty()) return
    while (seen.size > 500) seen.remove(seen.first())
    f.appendText(fresh.joinToString("") { it.toString() + "\n" })
    if (f.length() > MAX_BYTES) prune(f)
  }

  // Without per-message times (exactTime false) an updated notification gets a new post time, so the key leaves it out.
  private fun appLabel(context: Context, pkg: String): String = runCatching {
    val pm = context.packageManager
    pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
  }.getOrDefault(pkg)

  private fun entry(pkg: String, app: String, conversation: String, sender: String, text: String, time: Long, exactTime: Boolean): JSONObject? {
    if (text.isEmpty()) return null
    if (OTP.containsMatchIn(text) && DIGITS.containsMatchIn(text)) return null
    return JSONObject()
      .put("key", "$pkg|$conversation|$sender|$text|${if (exactTime) time else 0}")
      .put("time", time)
      .put("app", app)
      .put("conversation", conversation)
      .put("sender", sender)
      .put("text", text.take(2000))
  }

  private fun loadRecentKeys(f: File) {
    if (!f.exists()) return
    f.readLines().takeLast(500).forEach { line ->
      runCatching { JSONObject(line).optString("key") }.getOrNull()?.takeIf { it.isNotEmpty() }?.let(seen::add)
    }
  }

  private fun prune(f: File) {
    val cutoff = System.currentTimeMillis() - KEEP_MS
    val kept = f.readLines().filter { line -> runCatching { JSONObject(line).getLong("time") >= cutoff }.getOrDefault(false) }
    f.writeText(kept.takeLast(4000).joinToString("") { "$it\n" })
  }

  @Synchronized
  fun read(context: Context, sinceMs: Long, limit: Int): List<Map<String, Any>> {
    val f = file(context)
    if (!f.exists()) return emptyList()
    return f.readLines().mapNotNull { line ->
      runCatching {
        val o = JSONObject(line)
        if (o.getLong("time") < sinceMs) null else toMap(o)
      }.getOrNull()
    }.takeLast(limit)
  }

  private fun toMap(o: JSONObject): Map<String, Any> = mapOf(
    "time" to o.getLong("time").toDouble(),
    "app" to o.getString("app"),
    "conversation" to o.optString("conversation"),
    "sender" to o.optString("sender"),
    "text" to o.getString("text"),
  )

  /** Adds messages parsed from an export file. Returns how many were new. */
  @Synchronized
  fun importChats(context: Context, messages: List<Map<String, Any>>): Int {
    val f = File(context.filesDir, HISTORY)
    val known = HashSet<String>()
    if (f.exists()) f.forEachLine { line -> runCatching { JSONObject(line).optString("key") }.getOrNull()?.let(known::add) }
    val lines = StringBuilder()
    var added = 0
    for (m in messages) {
      val text = (m["text"] as? String)?.trim().orEmpty()
      val time = (m["time"] as? Number)?.toLong() ?: continue
      if (text.isEmpty()) continue
      val app = m["app"] as? String ?: "가져온 대화"
      val conversation = m["conversation"] as? String ?: ""
      val sender = m["sender"] as? String ?: ""
      val key = "$app|$conversation|$sender|$text|$time"
      if (!known.add(key)) continue
      lines.append(
        JSONObject().put("key", key).put("time", time).put("app", app).put("conversation", conversation)
          .put("sender", sender).put("text", text.take(4000)).toString()
      ).append('\n')
      added++
    }
    f.appendText(lines.toString())
    if (f.length() > MAX_HISTORY_BYTES) {
      val all = f.readLines()
      f.writeText(all.takeLast(all.size * 3 / 4).joinToString("") { "$it\n" })
    }
    return added
  }

  /** Searches saved notifications and imported chats for a word; newest matches last. */
  @Synchronized
  fun search(context: Context, keyword: String, sinceMs: Long, limit: Int): List<Map<String, Any>> {
    val needle = keyword.lowercase()
    val found = mutableListOf<JSONObject>()
    for (name in listOf(HISTORY, FILE)) {
      val f = File(context.filesDir, name)
      if (!f.exists()) continue
      f.forEachLine { line ->
        if (!line.lowercase().contains(needle)) return@forEachLine
        runCatching { JSONObject(line) }.getOrNull()?.takeIf { it.getLong("time") >= sinceMs }?.let(found::add)
      }
    }
    return found.sortedBy { it.getLong("time") }.takeLast(limit).map { toMap(it) }
  }

  @Synchronized
  fun historyCount(context: Context): Int {
    val f = File(context.filesDir, HISTORY)
    return if (f.exists()) f.readLines().count { it.isNotBlank() } else 0
  }

  @Synchronized
  fun count(context: Context): Int {
    val f = file(context)
    return if (f.exists()) f.readLines().count { it.isNotBlank() } else 0
  }

  @Synchronized
  fun clear(context: Context) {
    file(context).delete()
    File(context.filesDir, HISTORY).delete()
    seen.clear()
  }
}
