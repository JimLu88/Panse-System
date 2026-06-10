package com.aiworkbench.qianniu_agent.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import com.aiworkbench.qianniu_agent.MainActivity
import com.aiworkbench.qianniu_agent.auth.PairingAuth
import com.aiworkbench.qianniu_agent.http.HttpServer

/**
 * Phase 2.5 前台服务：保持 Ktor HTTP server 常驻。
 *
 * 行为：
 *   - onStartCommand → startForeground 显示通知（"千牛接待助手运行中 :8765"）
 *   - 拉起 HttpServer.start(this)
 *   - 进程被系统回收前会触发 onDestroy → HttpServer.stop()
 *
 * 通知设计：
 *   - 不可关闭（setOngoing(true)）
 *   - 点击通知 → 拉起 MainActivity
 *   - 通知文案含端口 + token 前 8 字符（debug 辅助；正式 token 仅二维码可见）
 *
 * Android 14+ specialUse 类型：
 *   - foregroundServiceType="specialUse" 已在 AndroidManifest 声明
 *   - PROPERTY_SPECIAL_USE_FGS_SUBTYPE 说明用途
 *   - 不需要 SYSTEM_ALERT_WINDOW 等危险权限
 */
class QianniuForegroundService : Service() {

    companion object {
        private const val TAG = "QianniuAgent.FgService"
        private const val CHANNEL_ID = "qianniu_agent_running"
        private const val CHANNEL_NAME = "千牛接待助手 · 后台运行"
        private const val NOTIFICATION_ID = 0x51_AB

        const val ACTION_START = "com.aiworkbench.qianniu_agent.ACTION_START_FG"
        const val ACTION_STOP = "com.aiworkbench.qianniu_agent.ACTION_STOP_FG"

        fun startIntent(ctx: Context): Intent =
            Intent(ctx, QianniuForegroundService::class.java).apply { action = ACTION_START }

        fun stopIntent(ctx: Context): Intent =
            Intent(ctx, QianniuForegroundService::class.java).apply { action = ACTION_STOP }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        ensureNotificationChannel()
        Log.i(TAG, "ForegroundService onCreate")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                Log.i(TAG, "收到 ACTION_STOP")
                stopHttpAndSelf()
                return START_NOT_STICKY
            }
            else -> {
                startForegroundCompat()
                try {
                    HttpServer.start(applicationContext)
                } catch (t: Throwable) {
                    Log.e(TAG, "HttpServer.start 失败", t)
                }
            }
        }
        // START_STICKY：被系统杀掉后会重启
        return START_STICKY
    }

    override fun onDestroy() {
        Log.i(TAG, "ForegroundService onDestroy")
        try {
            HttpServer.stop()
        } catch (_: Throwable) {}
        super.onDestroy()
    }

    private fun stopHttpAndSelf() {
        try {
            HttpServer.stop()
        } catch (_: Throwable) {}
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        if (nm.getNotificationChannel(CHANNEL_ID) != null) return
        val ch = NotificationChannel(
            CHANNEL_ID,
            CHANNEL_NAME,
            NotificationManager.IMPORTANCE_LOW,  // 不出声不震动
        ).apply {
            description = "保持 Ktor HTTP 服务（端口 8765）常驻，供 PC 端控制"
            setShowBadge(false)
        }
        nm.createNotificationChannel(ch)
    }

    private fun startForegroundCompat() {
        val token = try {
            PairingAuth.getOrCreateToken(applicationContext)
        } catch (_: Throwable) {
            ""
        }
        val tokenPrefix = if (token.length >= 8) token.substring(0, 8) else token

        val openIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pi = PendingIntent.getActivity(
            this, 0, openIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentTitle("千牛接待助手 · 运行中")
            .setContentText("HTTP :${HttpServer.PORT}  Token ${tokenPrefix}…")
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(pi)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE /* 34 */) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }
}
