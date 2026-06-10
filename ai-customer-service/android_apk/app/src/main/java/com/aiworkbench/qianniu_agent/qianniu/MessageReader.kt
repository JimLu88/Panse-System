package com.aiworkbench.qianniu_agent.qianniu

import android.graphics.Rect
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo

/**
 * 读取当前聊天页的消息气泡列表。
 *
 * 调用前提：
 *   - rootInActiveWindow 是某个买家的聊天页（已点开进入）
 *   - SessionListReader 之后切换 Activity 到 ChatActivity
 *
 * 气泡侧别判定逻辑：
 *   - 用气泡 left 坐标相对屏幕宽度的比例
 *   - leftRatio < 0.40 → 对方（LEFT，买家）
 *   - leftRatio > 0.50 → 自己（RIGHT，客服）
 *   - 中间灰区 → UNKNOWN（系统提示/居中通知）
 *
 * 阶段 1.5 验收：抽 20 条消息，左右判定正确率 ≥ 95%
 */
object MessageReader {

    private const val TAG = "QianniuAgent.Messages"

    // 气泡侧别判定阈值（屏幕宽度的比例）
    private const val LEFT_THRESHOLD = 0.40f
    private const val RIGHT_THRESHOLD = 0.50f

    /**
     * @param root            当前活动窗口 root
     * @param screenWidthPx   屏幕宽度（用于侧别判定）
     * @param maxCount        返回最近 N 条（默认 5；按 view tree 顺序的末尾 N）
     */
    fun read(
        root: AccessibilityNodeInfo?,
        screenWidthPx: Int,
        maxCount: Int = 5,
    ): List<QianniuMessage> {
        if (root == null) {
            Log.w(TAG, "root null")
            return emptyList()
        }
        if (screenWidthPx <= 0) {
            Log.w(TAG, "screenWidthPx 非法: $screenWidthPx")
            return emptyList()
        }

        // 1. 找聊天消息列表容器
        val container = Locators.CHAT_MESSAGE_LIST.firstNotNullOfOrNull { id ->
            root.findAccessibilityNodeInfosByViewId(id)?.firstOrNull()
        } ?: run {
            Log.w(TAG, "未找到聊天消息列表容器")
            return emptyList()
        }

        // 2. 抓所有气泡文本节点（按 Locators 优先级，第一个命中的就用）
        val bubbles = mutableListOf<QianniuMessage>()
        val now = System.currentTimeMillis()

        for (id in Locators.CHAT_BUBBLE_TEXT) {
            val nodes = container.findAccessibilityNodeInfosByViewId(id) ?: continue
            if (nodes.isEmpty()) continue

            for (node in nodes) {
                val text = node.text?.toString()?.trim()
                if (text.isNullOrEmpty()) continue
                val rect = Rect()
                node.getBoundsInScreen(rect)
                val side = classifySide(rect, screenWidthPx)
                bubbles.add(QianniuMessage(text = text, side = side, capturedAt = now))
            }

            // 第一个命中的 id 已经吃完所有气泡，不再用 fallback id（避免重复）
            if (bubbles.isNotEmpty()) break
        }

        if (bubbles.isEmpty()) {
            Log.w(TAG, "聊天容器找到但没有气泡文本节点")
            return emptyList()
        }

        // 3. 取最近 N 条（假定 view tree 顺序 = 时间顺序，末尾即最新）
        val tail = bubbles.takeLast(maxCount)
        Log.i(TAG, "read 成功：共 ${bubbles.size} 条，返回末尾 ${tail.size} 条")
        return tail
    }

    private fun classifySide(bounds: Rect, screenWidthPx: Int): BubbleSide {
        val leftRatio = bounds.left.toFloat() / screenWidthPx.toFloat()
        return when {
            leftRatio < LEFT_THRESHOLD -> BubbleSide.LEFT
            leftRatio > RIGHT_THRESHOLD -> BubbleSide.RIGHT
            else -> BubbleSide.UNKNOWN
        }
    }
}
