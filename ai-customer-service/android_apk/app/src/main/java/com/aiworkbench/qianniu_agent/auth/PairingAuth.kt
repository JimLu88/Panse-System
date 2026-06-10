package com.aiworkbench.qianniu_agent.auth

import android.content.Context
import android.content.SharedPreferences
import android.util.Base64
import java.security.SecureRandom

/**
 * 配对令牌（pairing token）：
 *   - APK 首次启动时生成 32 字节随机 token，写 SharedPreferences
 *   - 之后所有 HTTP 请求必须带 Authorization: Bearer <token> 才能通过
 *   - PC 端通过扫码（MainActivity 显示的二维码包含 {ip, port, token}）拿到 token
 *
 * 安全特性：
 *   - 用 SecureRandom（CSPRNG），不可预测
 *   - 32 字节 = 256 bit，暴力枚举不可行
 *   - SharedPreferences 是 app 私有目录，其他 app 无法读
 *
 * 重置场景：
 *   - 用户在 MainActivity 点「重新生成令牌」→ 调 resetToken()
 *   - 之前配过对的 PC 必须重新扫码
 */
object PairingAuth {

    private const val PREF_NAME = "qianniu_agent_prefs"
    private const val KEY_TOKEN = "pairing_token"
    private const val TOKEN_BYTES = 32

    private fun prefs(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)

    /** 取已有 token；不存在则生成并持久化。返回 URL-safe Base64（无 padding，便于二维码）。 */
    @Synchronized
    fun getOrCreateToken(ctx: Context): String {
        val sp = prefs(ctx)
        val existing = sp.getString(KEY_TOKEN, null)
        if (!existing.isNullOrBlank()) {
            return existing
        }
        val fresh = generateToken()
        sp.edit().putString(KEY_TOKEN, fresh).apply()
        return fresh
    }

    /** 强制重新生成 token（PC 端需要重新配对）。 */
    @Synchronized
    fun resetToken(ctx: Context): String {
        val fresh = generateToken()
        prefs(ctx).edit().putString(KEY_TOKEN, fresh).apply()
        return fresh
    }

    /**
     * 校验请求里的 token 与本地是否一致。
     * 常量时间比较以避免时序攻击（虽然 LAN 内攻击面有限，但成本是 0）。
     */
    fun verifyToken(ctx: Context, presented: String?): Boolean {
        if (presented.isNullOrBlank()) return false
        val expected = prefs(ctx).getString(KEY_TOKEN, null) ?: return false
        return constantTimeEquals(presented, expected)
    }

    private fun generateToken(): String {
        val buf = ByteArray(TOKEN_BYTES)
        SecureRandom().nextBytes(buf)
        // URL_SAFE + NO_WRAP + NO_PADDING：纯 ASCII，二维码字符集小
        return Base64.encodeToString(buf, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
    }

    private fun constantTimeEquals(a: String, b: String): Boolean {
        if (a.length != b.length) return false
        var diff = 0
        for (i in a.indices) {
            diff = diff or (a[i].code xor b[i].code)
        }
        return diff == 0
    }
}
