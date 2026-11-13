package expo.modules.dorothyinbox

import android.accessibilityservice.AccessibilityService
import android.graphics.Rect
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Watches messenger apps (KakaoTalk etc.) to notice when the OWNER sends a message, so Dorothy can
 * auto-clear a "회신 대기" without being told. It is limited to messenger packages (see the service's
 * android:packageNames), records only {app, room-hint, time} — never the message text — and runs only
 * after the user grants accessibility access.
 *
 * Detection is deliberately version-independent, because KakaoTalk's view ids change between releases:
 *   1) the chat's text input going non-empty -> empty (what happens the instant you tap send), and
 *   2) a click on a control whose label looks like "send" (전송 / 보내기 / Send) while the input had text.
 * The room name is captured best-effort from the SHORT texts in the top strip of the screen (where the
 * title sits), filtered by on-screen position so message bubbles are not stored.
 */
class DorothyReplyListener : AccessibilityService() {
  private var lastInput: String = ""
  private var lastPkg: String = ""
  private var lastRecordAt: Long = 0L

  override fun onAccessibilityEvent(event: AccessibilityEvent?) {
    val e = event ?: return
    val pkg = e.packageName?.toString() ?: return
    if (!InboxStore.APPS.containsKey(pkg)) return
    try {
      when (e.eventType) {
        AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED -> {
          val node = e.source ?: return
          if (!node.isEditable) return
          val text = e.text?.joinToString(" ")?.trim().orEmpty()
          // A real message was in the box and it just emptied -> treated as a send.
          if (text.isEmpty() && lastInput.isNotEmpty() && lastPkg == pkg) recordSend(pkg)
          lastInput = text
          lastPkg = pkg
        }
        AccessibilityEvent.TYPE_VIEW_CLICKED -> {
          val node = e.source ?: return
          val label = ((node.contentDescription ?: "").toString() + " " + (node.text ?: "").toString()).lowercase()
          if (lastInput.isNotEmpty() && lastPkg == pkg && SEND_WORDS.any { label.contains(it) }) {
            recordSend(pkg)
            lastInput = ""
          }
        }
      }
    } catch (t: Throwable) {
      Log.w("DorothyInbox", "reply watch", t)
    }
  }

  private fun recordSend(pkg: String) {
    val now = System.currentTimeMillis()
    // The input-clear and the send-click can both fire for one message; collapse them.
    if (now - lastRecordAt < 4000) return
    lastRecordAt = now
    SentStore.record(applicationContext, pkg, InboxStore.APPS[pkg] ?: pkg, roomHints(), now)
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
