package com.aiworkbench.qianniu_agent.humanize

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import java.util.Calendar
import kotlin.math.max
import kotlin.random.Random

/**
 * 行为级拟人化（Kotlin 移植 PC 端 reply_timing/typing_real/mouse_jitter 的核心逻辑）。
 *
 * 为什么节奏控制下沉到 APK：
 *   - 节奏抖动在设备本地实现更真实（不受 PC 端 HTTP 调用延迟噪声影响）
 *   - 跨网络 HTTP 调用引入的延迟反而像机器规律
 *
 * 三类能力：
 *   1. computeReplyDelayMs(text) — 回复延迟（基础 8-20s + 长消息加权）
 *   2. isInQuietHours() — 深夜降级（凌晨 1-7 点）
 *   3. humanizedTap(service, node) — 节点中心 ±N px 抖动 + 80-160ms 持续时长手势
 *
 * 默认值与 PC 端保持一致；如需差异化由 SharedPreferences 配（暂未做 UI，先 hardcode）。
 */
object HumanBehavior {

    // ── 回复延迟参数（与 PC 端 ReplyTimingSettings 默认值对齐）─────
    const val REPLY_DELAY_MIN_MS = 8_000L
    const val REPLY_DELAY_MAX_MS = 20_000L
    const val TYPING_EXTRA_MS_PER_BLOCK = 30_000L
    const val TYPING_BLOCK_CHARS = 200
    const val GAUSSIAN_JITTER_RATIO = 0.15

    // ── 深夜降级 ─────────────────────────────────────────────────
    const val QUIET_HOURS_START = 1   // 凌晨 1 点
    const val QUIET_HOURS_END = 7     // 早 7 点

    // ── 点击 / 手势参数 ──────────────────────────────────────────
    const val TAP_JITTER_PX = 5
    const val TAP_DURATION_MIN_MS = 80L
    const val TAP_DURATION_MAX_MS = 160L

    /**
     * 算一条回复应该等多久才发出去。
     *
     * 公式：
     *   base = uniform(min, max)
     *   typing_extra = (chars // block) * 30s     ← 整除而非 ceil，避免短消息加权
     *   raw = base + typing_extra
     *   return gaussian_jitter(raw, ratio=0.15)
     */
    fun computeReplyDelayMs(text: String): Long {
        val base = randomLongUniform(REPLY_DELAY_MIN_MS, REPLY_DELAY_MAX_MS)
        val chars = text.length
        val blocks = if (chars > 0 && TYPING_BLOCK_CHARS > 0) chars / TYPING_BLOCK_CHARS else 0
        val typingExtra = blocks * TYPING_EXTRA_MS_PER_BLOCK
        val raw = base + typingExtra
        return jitterMs(raw, GAUSSIAN_JITTER_RATIO).coerceAtLeast(0L)
    }

    /** 正态分布扰动：base ± gauss(0, base*ratio)。 */
    fun jitterMs(baseMs: Long, ratio: Double): Long {
        if (ratio <= 0) return baseMs
        val sigma = baseMs.toDouble() * ratio
        val noise = randomGaussian(0.0, sigma)
        return max(0L, baseMs + noise.toLong())
    }

    /** 当前时间是否在「深夜降级」时段。跨午夜（start > end）按 OR 判定。 */
    fun isInQuietHours(now: Calendar = Calendar.getInstance()): Boolean {
        val hr = now.get(Calendar.HOUR_OF_DAY)
        val start = QUIET_HOURS_START % 24
        val end = QUIET_HOURS_END % 24
        if (start == end) return false
        return if (start < end) {
            hr in start until end
        } else {
            hr >= start || hr < end
        }
    }

    /**
     * 拟人化点击节点：中心点 ± TAP_JITTER_PX 抖动 + 80-160ms 持续 dispatchGesture。
     *
     * @return true=手势已派发；false=节点 bounds 非法 / 派发失败
     */
    fun humanizedTap(
        service: AccessibilityService,
        node: AccessibilityNodeInfo,
    ): Boolean {
        val rect = Rect()
        node.getBoundsInScreen(rect)
        if (rect.width() <= 0 || rect.height() <= 0) return false

        val cx = rect.centerX() + Random.nextInt(-TAP_JITTER_PX, TAP_JITTER_PX + 1)
        val cy = rect.centerY() + Random.nextInt(-TAP_JITTER_PX, TAP_JITTER_PX + 1)
        val durationMs = randomLongUniform(TAP_DURATION_MIN_MS, TAP_DURATION_MAX_MS)

        val path = Path().apply { moveTo(cx.toFloat(), cy.toFloat()) }
        val stroke = GestureDescription.StrokeDescription(path, 0L, durationMs)
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        return service.dispatchGesture(gesture, null, null)
    }

    // ── 内部工具 ─────────────────────────────────────────────────

    private fun randomLongUniform(lo: Long, hi: Long): Long {
        if (hi <= lo) return lo
        return lo + Random.nextLong(hi - lo + 1)
    }

    /** Box-Muller 生成 N(mean, sigma) 的样本。kotlin.random 没原生 gauss。 */
    private fun randomGaussian(mean: Double, sigma: Double): Double {
        var u1 = Random.nextDouble()
        if (u1 < 1e-12) u1 = 1e-12  // 防 log(0)
        val u2 = Random.nextDouble()
        val z = Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2)
        return mean + z * sigma
    }
}
