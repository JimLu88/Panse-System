package com.aiworkbench.qianniu_agent.qianniu

/**
 * 千牛 App 的 AccessibilityNode 定位字典。
 *
 * 设计原则（与 v1.4.0 LOCATORS 字典对齐）：
 *   - 所有 resource-id 集中在此，千牛 App 改版只改这一处
 *   - 每个键提供主选 + 备选
 *   - 不写死坐标
 *
 * ⚠️ 这些值是阶段 1 的初始猜测，需要在真机上用 `adb shell uiautomator dump`
 *   或 Android Studio Layout Inspector 实际抓取后修正。
 */
object Locators {

    /** 千牛 App 包名 — 用于 AccessibilityService 过滤 */
    const val QIANNIU_PACKAGE = "com.taobao.qianniu"

    // ── 主界面 / 底部导航 ────────────────────────────────────────────
    val TAB_MESSAGE = listOf(
        "com.taobao.qianniu:id/tab_message",
        "com.taobao.qianniu:id/nav_message"
    )

    // ── 会话列表页 ────────────────────────────────────────────────────
    val SESSION_LIST = listOf(
        "com.taobao.qianniu:id/recycler_view",
        "com.taobao.qianniu:id/session_list",
        "com.taobao.qianniu:id/conversation_list"
    )
    val SESSION_ITEM_NAME = listOf(
        "com.taobao.qianniu:id/tv_name",
        "com.taobao.qianniu:id/buyer_name",
        "com.taobao.qianniu:id/tv_nick"
    )
    val SESSION_ITEM_UNREAD = listOf(
        "com.taobao.qianniu:id/tv_unread_count",
        "com.taobao.qianniu:id/unread_badge"
    )
    val SESSION_ITEM_PREVIEW = listOf(
        "com.taobao.qianniu:id/tv_last_msg",
        "com.taobao.qianniu:id/tv_preview"
    )

    // ── 聊天页 ────────────────────────────────────────────────────────
    val CHAT_MESSAGE_LIST = listOf(
        "com.taobao.qianniu:id/chat_list",
        "com.taobao.qianniu:id/message_list_view",
        "com.taobao.qianniu:id/recycler_view"
    )
    val CHAT_BUBBLE_TEXT = listOf(
        "com.taobao.qianniu:id/tv_chat_text",
        "com.taobao.qianniu:id/message_text",
        "com.taobao.qianniu:id/content_text"
    )
    val CHAT_TITLE = listOf(
        "com.taobao.qianniu:id/tv_title",
        "com.taobao.qianniu:id/title_bar_title"
    )
    val CHAT_INPUT = listOf(
        "com.taobao.qianniu:id/et_input",
        "com.taobao.qianniu:id/chat_input",
        "com.taobao.qianniu:id/input_text"
    )
    val CHAT_SEND_BTN = listOf(
        "com.taobao.qianniu:id/btn_send",
        "com.taobao.qianniu:id/send_btn"
    )

    val BACK_BTN = listOf(
        "com.taobao.qianniu:id/back",
        "com.taobao.qianniu:id/iv_back"
    )
}
