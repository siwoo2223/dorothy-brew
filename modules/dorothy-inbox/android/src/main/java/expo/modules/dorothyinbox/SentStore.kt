package expo.modules.dorothyinbox

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Records that the OWNER sent a message in a messenger (KakaoTalk etc.), so Dorothy can auto-clear a
 * "회신 대기" without being told. Only {time, app, roomHints} is kept — never the message text, and
 * roomHints are just the short texts near the top of the screen (the room/person title), captured so
 * the reply can be matched to the right follow-up. Lives in the app's private storage; nothing leaves
 * the phone. The accessibility service feeds this; the app reads it to reconcile the follow-up list.
 */
object SentStore {
  private const val FILE = "dorothy-sent.jsonl"
  private const val KEEP_MS = 14L * 24 * 60 * 60 * 1000
  private const val MAX_BYTES = 256L * 1024

  private fun file(context: Context) = File(context.filesDir, FILE)

  @Synchronized
  fun record(context: Context, pkg: String, app: String, hints: List<String>, time: Long) {
    val o = JSONObject()
      .put("time", time)
      .put("app", app)
      .put("pkg", pkg)
      .put("hints", JSONArray(hints))
    val f = file(context)
    f.appendText(o.toString() + "\n")
    if (f.length() > MAX_BYTES) prune(f)
  }

  private fun prune(f: File) {
    val cutoff = System.currentTimeMillis() - KEEP_MS
    val kept = f.readLines().filter { line ->
      runCatching { JSONObject(line).getLong("time") >= cutoff }.getOrDefault(false)
    }
    f.writeText(kept.takeLast(400).joinToString("") { "$it\n" })
  }

  @Synchronized
  fun read(context: Context, sinceMs: Long, limit: Int): List<Map<String, Any>> {
    val f = file(context)
    if (!f.exists()) return emptyList()
    return f.readLines().mapNotNull { line ->
      runCatching {
        val o = JSONObject(line)
        if (o.getLong("time") < sinceMs) return@runCatching null
        val arr = o.optJSONArray("hints") ?: JSONArray()
        val hints = (0 until arr.length()).map { arr.getString(it) }
        mapOf(
          "time" to o.getLong("time").toDouble(),
          "app" to o.optString("app"),
          "hints" to hints,
        )
      }.getOrNull()
    }.takeLast(limit)
  }

  @Synchronized
  fun clear(context: Context) {
    file(context).delete()
  }

  // --- Diagnostics: a short ring of what the accessibility service actually receives, so a phone
  // where detection misses can be tuned from real data. Off by default; includes short text snippets
  // only while the owner turns it on to troubleshoot. ---
  private const val DEBUG_FILE = "dorothy-sent-debug.jsonl"
  private const val DEBUG_PREF = "dorothy_sent"
  private const val DEBUG_KEY = "debug"
  private const val DEBUG_MAX = 60

  fun setDebug(context: Context, on: Boolean) {
    context.getSharedPreferences(DEBUG_PREF, Context.MODE_PRIVATE).edit().putBoolean(DEBUG_KEY, on).apply()
    if (!on) File(context.filesDir, DEBUG_FILE).delete()
  }

  fun debugEnabled(context: Context): Boolean =
    context.getSharedPreferences(DEBUG_PREF, Context.MODE_PRIVATE).getBoolean(DEBUG_KEY, false)

  @Synchronized
  fun debug(context: Context, kind: String, text: String) {
    val f = File(context.filesDir, DEBUG_FILE)
    val o = JSONObject().put("time", System.currentTimeMillis()).put("kind", kind).put("text", text.take(40))
    f.appendText(o.toString() + "\n")
    val lines = f.readLines()
    if (lines.size > DEBUG_MAX) f.writeText(lines.takeLast(DEBUG_MAX).joinToString("") { "$it\n" })
  }

  @Synchronized
  fun readDebug(context: Context, limit: Int): List<Map<String, Any>> {
    val f = File(context.filesDir, DEBUG_FILE)
    if (!f.exists()) return emptyList()
    return f.readLines().mapNotNull { line ->
      runCatching {
        val o = JSONObject(line)
        mapOf("time" to o.getLong("time").toDouble(), "kind" to o.optString("kind"), "text" to o.optString("text"))
      }.getOrNull()
    }.takeLast(limit)
  }
}
