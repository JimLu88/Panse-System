package com.aiworkbench.qianniu_agent.qianniu

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo

/**
 * 在当前聊天页发一条消息。
 *
 * 两步走：
 *   1. 输入框 ← ACTION_SET_TEXT（无障碍标准 API，比模拟键盘更稳）
 *   2. 发送按钮 ← dispatchGesture（真实坐标 + 80-160ms 持续 + ±5px 抖动）
 *
 * 为什么不用 performAction(ACTION_CLICK)：
 *   - ACTION_CLICK 是虚拟点击，千牛风控可通过 MotionEvent 缺失检出
 *   - dispatchGesture 触发真实的 InputManager 事件流，与真人触摸一致
 *
 * 阶段 1.6 验收：在测试买家会话里发出 "test from accessibility service"，
 * 千牛对方可见，且输入框被清空。
 */
object MessageSender {

    private const val TAG = "QianniuAgent.Sender"

    // 点击行为拟人化参数
    private const val CLICK_JITTER_PX = 5
    private const val CLICK_DURATION_MIN_MS = 80L
    private const val CLICK_DURATION_MAX_MS = 160L

    /**
     * @return true=发送动作已触发；false=未找到输入框/按钮，或 SET_TEXT/手势失败
     */
    fun send(service: AccessibilityService, text: String): Boolean {
        if (text.isBlank()) {
            Log.w(TAG, "send 跳过：text 为空")
            return false
        }

        val root = service.rootInActiveWindow ?: run {
            Log.w(TAG, "root null（窗口未就绪）")
            return false
        }

        // 1. 输入框
        val input = findFirstByIds(root, Locators.CHAT_INPUT) ?: run {
            Log.w(TAG, "未找到输入框（[CHAT_INPUT] 全部未命中）")
            return false
        }

        // 2. ACTION_SET_TEXT
        val args = Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                text,
            )
        }
        val setOk = input.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        if (!setOk) {
            Log.w(TAG, "ACTION_SET_TEXT 失败")
            return false
        }

        // 3. 发送按钮 —— 重新拿 root，因为 SET_TEXT 后 view tree 可能刷新
        val rootAfter = service.rootInActiveWindow ?: root
        val sendBtn = findFirstByIds(rootAfter, Locators.CHAT_SEND_BTN) ?: run {
            Log.w(TAG, "未找到发送按钮（[CHAT_SEND_BTN] 全部未命中）")
            return false
        }

        return tapWithGesture(service, sendBtn)
    }

    /**
     * 在屏幕绝对坐标 (x, y) 触发一次拟人化点击（±5px 抖动 + 80-160ms 持续）。
     * Phase 2.3 HttpServer 的 POST /switch 路由会调这个来点会话条进入聊天页。
     */
    fun tapAt(service: AccessibilityService, x: Int, y: Int): Boolean {
        if (x < 0 || y < 0) {
            Log.w(TAG, "tapAt 坐标非法: ($x,$y)")
            return false
        }
        val cx = x + (-CLICK_JITTER_PX..CLICK_JITTER_PX).random()
        val cy = y + (-CLICK_JITTER_PX..CLICK_JITTER_PX).random()
        val durationMs = (CLICK_DURATION_MIN_MS..CLICK_DURATION_MAX_MS).random()
        val path = Path().apply { moveTo(cx.toFloat(), cy.toFloat()) }
        val stroke = GestureDescription.StrokeDescription(path, 0L, durationMs)
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        val dispatched = service.dispatchGesture(gesture, null, null)
        Log.i(TAG, "tapAt($x,$y) → 实际($cx,$cy) duration=${durationMs}ms dispatched=$dispatched")
        return dispatched
    }

    private fun tapWithGesture(
        service: AccessibilityService,
        node: AccessibilityNodeInfo,
    ): Boolean {
        val rect = Rect()
        node.getBoundsInScreen(rect)
        if (rect.width() <= 0 || rect.height() <= 0) {
            Log.w(TAG, "tapWithGesture 节点 bounds 非法: $rect")
            return false
        }

        // 中心点 + 随机抖动
        val cx = rect.centerX() + (-CLICK_JITTER_PX..CLICK_JITTER_PX).random()
        val cy = rect.centerY() + (-CLICK_JITTER_PX..CLICK_JITTER_PX).random()
        val durationMs = (CLICK_DURATION_MIN_MS..CLICK_DURATION_MAX_MS).random()

        val path = Path().apply { moveTo(cx.toFloat(), cy.toFloat()) }
        val stroke = GestureDescription.StrokeDescription(path, 0L, durationMs)
        val gesture = GestureDescription.Builder().addStroke(stroke).build()

        val dispatched = service.dispatchGesture(gesture, null, null)
        Log.i(TAG, "tap 完成：($cx,$cy) duration=${durationMs}ms dispatched=$dispatched")
        return dispatched
    }

    private fun findFirstByIds(root: AccessibilityNodeInfo, ids: List<String>): AccessibilityNodeInfo? {
        for (id in ids) {
            val nodes = root.findAccessibilityNodeInfosByViewId(id)
            val first = nodes?.firstOrNull()
            if (first != null) return first
        }
        return null
    }
}
