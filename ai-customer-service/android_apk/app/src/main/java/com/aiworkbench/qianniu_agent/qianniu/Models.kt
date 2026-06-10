package com.aiworkbench.qianniu_agent.qianniu

/**
 * 千牛 App view tree 解析得到的数据模型。
 *
 * 设计原则：
 *   - 纯 data class，方便 Phase 2 直接 JSON 序列化（Ktor / kotlinx.serialization）
 *   - 不依赖 Android 框架类型（除 Int / String / Long），便于单元测试
 *   - 屏幕坐标保留为 Int，PC 端调试时可视化用
 */

/**
 * 会话列表中的一项。
 *
 * @param name          买家昵称（必填，空名直接跳过）
 * @param unreadCount   未读消息数；0 表示无未读
 * @param previewText   最新一条消息预览；千牛某些版本不显示则为 null
 * @param boundsLeft    会话条在屏幕中的左 x 坐标（用于 Phase 1.6 点击进入）
 * @param boundsTop     上 y
 * @param boundsRight   右 x
 * @param boundsBottom  下 y
 */
data class QianniuSession(
    val name: String,
    val unreadCount: Int,
    val previewText: String?,
    val boundsLeft: Int,
    val boundsTop: Int,
    val boundsRight: Int,
    val boundsBottom: Int,
)

/**
 * 聊天页中的一条消息（气泡）。
 *
 * @param text       消息文本
 * @param side       气泡侧别（左=对方/买家，右=自己/卖家）
 * @param capturedAt 抓取时间戳（毫秒，System.currentTimeMillis()）；非消息真实发送时间
 */
data class QianniuMessage(
    val text: String,
    val side: BubbleSide,
    val capturedAt: Long,
)

/**
 * 气泡相对屏幕中点的侧别。
 *
 * - LEFT：对方（买家）的消息
 * - RIGHT：自己（卖家/客服）的消息
 * - UNKNOWN：系统提示 / 居中通知 / 判定失败
 */
enum class BubbleSide {
    LEFT,
    RIGHT,
    UNKNOWN,
}
