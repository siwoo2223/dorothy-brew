package expo.modules.dorothyinbox

import android.accessibilityservice.AccessibilityService
import android.graphics.Rect
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Watches messenger apps (KakaoTalk etc.) to notice when the OWNER sends a message, so Dorothy can
 * auto-clear a "회신 대기" without being told. Limited to messenger packages (see android:packageNames),
 * records only {app, room-hint, time} — never the message text — and runs only after accessibility is
 * granted.
 *
 * KakaoTalk's view ids change between releases, so detection uses signals that do not depend on them:
 *   1) the typed text appearing as a NEW on-screen node (your message becomes a bubble the instant you
 *      send) — the strongest, most version-proof signal;
 *   2) the chat input going non-empty -> empty (the box clears on send);
 *   3) a click on a control labelled like "send" (전송 / 보내기 / Send) while the input had text.
 * The room name is captured best-effort from the SHORT texts in the top strip of the screen.
 *
 * A debug toggle (SentStore.setDebug) logs what the service actually receives from a messenger, so a
 * phone where detection misses can be tuned from real data instead of guesswork.
 */
class DorothyReplyListener : AccessibilityService() {
  private var lastInput: String = ""
  private var lastPkg: String = ""
  private var lastRecordAt: Long = 0L

  override fun onAccessibilityEvent(event: AccessibilityEvent?) {
    val e = event ?: return
    val pkg = e.packageName?.toString() ?: return
    if (!InboxStore.APPS.containsKey(pkg)) return
    val debug = SentStore.debugEnabled(applicationContext)
    try {
      when (e.eventType) {
        AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED -> {
          val node = e.source ?: return
          if (!node.isEditable) return
          val text = e.text?.joinToString(" ")?.trim().orEmpty()
          if (debug) SentStore.debug(applicationContext, "input", text)
          // The box emptied after holding a real message -> a send.
          if (text.isEmpty() && lastInput.isNotEmpty() && lastPkg == pkg) recordSend(pkg, "clear")
          lastInput = text
          lastPkg = pkg
        }
        AccessibilityEvent.TYPE_VIEW_CLICKED -> {
          val node = e.source ?: return
          val label = ((node.contentDescription ?: "").toString() + " " + (node.text ?: "").toString()).trim()
          if (debug) SentStore.debug(applicationContext, "click", label)
          if (lastInput.isNotEmpty() && lastPkg == pkg && SEND_WORDS.any { label.lowercase().contains(it) }) {
            recordSend(pkg, "sendbtn")
            lastInput = ""
          }
        }
        AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED,
        AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> {
          // The surest signal: the text you typed just showed up as a (non-editable) bubble.
          if (lastInput.length >= 1 && lastPkg == pkg) {
            val root = rootInActiveWindow
            if (root != null) {
              val appeared = try {
                containsBubble(root, lastInput, 0)
              } finally {
                @Suppress("DEPRECATION") root.recycle()
              }
              if (appeared) {
                recordSend(pkg, "bubble")
                lastInput = ""
              }
            }
          }
        }
      }
    } catch (t: Throwable) {
      Log.w("DorothyInbox", "reply watch", t)
    }
  }

  private fun recordSend(pkg: String, how: String) {
    val now = System.currentTimeMillis()
    // The signals can double-fire for one message; collapse them.
    if (now - lastRecordAt < 4000) return
    lastRecordAt = now
    val hints = roomHints()
    if (SentStore.debugEnabled(applicationContext)) SentStore.debug(applicationContext, "SENT($how)", hints.joinToString(" / "))
    SentStore.record(applicationContext, pkg, InboxStore.APPS[pkg] ?: pkg, hints, now)
  }

  /** True if a non-editable node holds exactly the given text (the sent message, now a bubble). */
  private fun containsBubble(node: AccessibilityNodeInfo?, target: String, depth: Int): Boolean {
    if (node == null || depth > 40) return false
    val t = node.text?.toString()?.trim()
    if (!t.isNullOrEmpty() && !node.isEditable && t == target) return true
    for (i in 0 until node.childCount) {
      if (containsBubble(node.getChild(i), target, depth + 1)) return true
    }
    return false
  }

  /** Short texts in the top ~18% of the screen — that's where the room/person title sits. */
  private fun roomHints(): List<String> {
    val root = rootInActiveWindow ?: return emptyList()
    return try {
      val screen = Rect()
      root.getBoundsInScreen(screen)
      val limitY = screen.top + (screen.height() * 0.18).toInt()
      val out = ArrayList<String>()
      collectTop(root, out, limitY, Rect(), 0)
      out.distinct().take(4)
    } finally {
      @Suppress("DEPRECATION")
      root.recycle()
    }
  }

  private fun collectTop(node: AccessibilityNodeInfo?, out: MutableList<String>, limitY: Int, r: Rect, depth: Int) {
    if (node == null || out.size >= 8 || depth > 25) return
    val t = node.text?.toString()?.trim()
    if (!t.isNullOrEmpty() && t.length in 1..24 && !t.contains('\n')) {
      node.getBoundsInScreen(r)
      if (r.top in 0..limitY) out.add(t)
    }
    for (i in 0 until node.childCount) collectTop(node.getChild(i), out, limitY, r, depth + 1)
  }

  override fun onInterrupt() {}

  companion object {
    private val SEND_WORDS = listOf("전송", "보내기", "send")
  }
}
