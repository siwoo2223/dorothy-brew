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
}
