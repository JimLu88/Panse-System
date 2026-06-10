package com.aiworkbench.qianniu_agent

import android.accessibilityservice.AccessibilityService
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.aiworkbench.qianniu_agent.qianniu.Locators

/**
 * 千牛接待无障碍服务。
 *
 * 阶段 1 实现：
 *   - 监听千牛 App 页面切换（onAccessibilityEvent）
 *   - 接收到事件后，把 active window 的 view tree 关键节点 dump 到 logcat
 *   - 暂不暴露 HTTP 接口（阶段 2 接入）
 *
 * 用户验证步骤：
 *   1. 安装 APK → 系统设置 → 无障碍 → 启用「千牛接待助手」
 *   2. 打开千牛 App，切换到「消息」页面
 *   3. PC 上执行：adb logcat -s QianniuAgent
 *   4. 应看到当前 Activity 名 + 会话列表 dump
 */
class QianniuAccessibilityService : AccessibilityService() {

    companion object {
        private const val TAG = "QianniuAgent"
        @Volatile
        var instance: QianniuAccessibilityService? = null
            private set
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "═══ AccessibilityService 已连接 ═══")
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
        Log.i(TAG, "AccessibilityService 已断开")
    }

    override fun onInterrupt() {
        Log.w(TAG, "AccessibilityService onInterrupt")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        event ?: return
        if (event.packageName?.toString() != Locators.QIANNIU_PACKAGE) return

        when (event.eventType) {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> {
                val activity = event.className?.toString() ?: "?"
                Log.i(TAG, "→ 千牛页面切换: $activity")
                dumpKeyNodes("page_change")
            }
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> {
                // 内容变化非常频繁，阶段 1 不 dump，阶段 2 才用
            }
            else -> {}
        }
    }

    /**
     * 把当前活动窗口的关键节点（资源 ID 在 Locators 字典里的）打印到 logcat。
     * 阶段 1 用于验证：千牛的 resource-id 我们是否猜对了。
     */
    private fun dumpKeyNodes(reason: String) {
        val root = rootInActiveWindow
        if (root == null) {
            Log.w(TAG, "[$reason] rootInActiveWindow == null（窗口暂未就绪）")
            return
        }
        Log.i(TAG, "─── dump 开始 reason=$reason ───")

        val allIdGroups: List<Pair<String, List<String>>> = listOf(
            "TAB_MESSAGE"          to Locators.TAB_MESSAGE,
            "SESSION_LIST"         to Locators.SESSION_LIST,
            "SESSION_ITEM_NAME"    to Locators.SESSION_ITEM_NAME,
            "SESSION_ITEM_UNREAD"  to Locators.SESSION_ITEM_UNREAD,
            "SESSION_ITEM_PREVIEW" to Locators.SESSION_ITEM_PREVIEW,
            "CHAT_MESSAGE_LIST"    to Locators.CHAT_MESSAGE_LIST,
            "CHAT_BUBBLE_TEXT"     to Locators.CHAT_BUBBLE_TEXT,
            "CHAT_TITLE"           to Locators.CHAT_TITLE,
            "CHAT_INPUT"           to Locators.CHAT_INPUT,
            "CHAT_SEND_BTN"        to Locators.CHAT_SEND_BTN,
        )

        var anyHit = false
        for ((label, ids) in allIdGroups) {
            for (id in ids) {
                val nodes = root.findAccessibilityNodeInfosByViewId(id)
                if (!nodes.isNullOrEmpty()) {
                    anyHit = true
                    Log.i(TAG, "  [$label] id=$id 命中 ${nodes.size} 个节点")
                    nodes.take(3).forEachIndexed { i, n ->
                        Log.i(TAG, "    #$i text=${n.text?.toString()?.take(40)} desc=${n.contentDescription}")
                    }
                }
            }
        }

        if (!anyHit) {
            Log.w(TAG, "⚠️ Locators 字典全部未命中，dump 整树前 80 个节点：")
            dumpTreeRecursive(root, 0, 80, intArrayOf(0))
        }

        Log.i(TAG, "─── dump 结束 ───")
    }

    private fun dumpTreeRecursive(
        node: AccessibilityNodeInfo?,
        depth: Int,
        maxCount: Int,
        counter: IntArray
    ) {
        if (node == null || counter[0] >= maxCount) return
        counter[0]++
        val pad = "  ".repeat(depth)
        val id = node.viewIdResourceName ?: "—"
        val text = node.text?.toString()?.take(30) ?: ""
        val desc = node.contentDescription?.toString()?.take(20) ?: ""
        Log.i(TAG, "$pad[${node.className}] id=$id text=$text desc=$desc")
        for (i in 0 until node.childCount) {
            dumpTreeRecursive(node.getChild(i), depth + 1, maxCount, counter)
            if (counter[0] >= maxCount) return
        }
    }
}
