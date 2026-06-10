package com.aiworkbench.qianniu_agent.qianniu

import android.graphics.Rect
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo

/**
 * 读取千牛"消息"页面的会话列表。
 *
 * 调用前提：
 *   - rootInActiveWindow 已经是千牛 App 的消息 Activity
 *   - 用户先手动点过底部 [Locators.TAB_MESSAGE]，或会话列表是默认页
 *
 * 阶段 1.4 验收：
 *   - logcat 输出的会话数与手机屏幕上肉眼可数的会话数一致
 *   - 未读数与手机角标一致
 *   - 与 v1.4.0 uiautomator2 实现对比，会话名一致
 */
object SessionListReader {

    private const val TAG = "QianniuAgent.Sessions"

    /**
     * 读取当前活动窗口的会话列表。
     * @return 会话列表；如果未找到列表容器返回空 list（不抛异常，方便 HTTP 路由直接返回）
     */
    fun read(root: AccessibilityNodeInfo?): List<QianniuSession> {
        if (root == null) {
            Log.w(TAG, "rootInActiveWindow == null（窗口未就绪）")
            return emptyList()
        }

        // 1. 找会话列表容器（一般是 RecyclerView）
        val container = findFirstByIds(root, Locators.SESSION_LIST)
        if (container == null) {
            Log.w(TAG, "未找到会话列表容器，当前可能不在消息页")
            return emptyList()
        }

        // 2. 遍历直接子项 = 每个会话条
        val sessions = mutableListOf<QianniuSession>()
        for (i in 0 until container.childCount) {
            val item = container.getChild(i) ?: continue
            val session = parseItem(item)
            if (session != null) sessions.add(session)
        }

        Log.i(TAG, "read 成功：${sessions.size} 个会话")
        return sessions
    }

    private fun parseItem(item: AccessibilityNodeInfo): QianniuSession? {
        // 必填：昵称（找不到则该 item 跳过，可能是分组标题/广告位）
        val name = findFirstTextByIds(item, Locators.SESSION_ITEM_NAME) ?: return null

        // 可选：未读数（千牛的角标是 String，要 toIntOrNull 容错 "99+" 等）
        val unreadRaw = findFirstTextByIds(item, Locators.SESSION_ITEM_UNREAD)
        val unread = parseUnread(unreadRaw)

        // 可选：预览
        val preview = findFirstTextByIds(item, Locators.SESSION_ITEM_PREVIEW)

        val rect = Rect()
        item.getBoundsInScreen(rect)

        return QianniuSession(
            name = name,
            unreadCount = unread,
            previewText = preview,
            boundsLeft = rect.left,
            boundsTop = rect.top,
            boundsRight = rect.right,
            boundsBottom = rect.bottom,
        )
    }

    /** "99+" → 99；"3" → 3；null / 非数字 → 0 */
    private fun parseUnread(raw: String?): Int {
        if (raw.isNullOrBlank()) return 0
        val digits = raw.filter { it.isDigit() }
        return digits.toIntOrNull() ?: 0
    }

    private fun findFirstByIds(root: AccessibilityNodeInfo, ids: List<String>): AccessibilityNodeInfo? {
        for (id in ids) {
            val nodes = root.findAccessibilityNodeInfosByViewId(id)
            val first = nodes?.firstOrNull()
            if (first != null) return first
        }
        return null
    }

    private fun findFirstTextByIds(scope: AccessibilityNodeInfo, ids: List<String>): String? {
        for (id in ids) {
            val nodes = scope.findAccessibilityNodeInfosByViewId(id)
            for (n in nodes) {
                val t = n.text?.toString()?.trim()
                if (!t.isNullOrEmpty()) return t
            }
        }
        return null
    }
}
