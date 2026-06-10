package com.aiworkbench.qianniu_agent

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.aiworkbench.qianniu_agent.auth.PairingAuth
import com.aiworkbench.qianniu_agent.databinding.ActivityMainBinding
import com.aiworkbench.qianniu_agent.http.HttpServer
import com.aiworkbench.qianniu_agent.service.QianniuForegroundService
import com.aiworkbench.qianniu_agent.util.NetworkUtil
import com.aiworkbench.qianniu_agent.util.QrCodeUtil

/**
 * 主界面：
 *   - 显示无障碍授权状态 + 跳转系统设置按钮
 *   - 显示 HTTP 服务状态 + IP:Port + 启动/停止按钮
 *   - 显示配对二维码（含 {ip, port, token} JSON）+ 重置 token 按钮
 *
 * 配对二维码 JSON 示例：
 *   {"ip":"192.168.1.42","port":8765,"token":"abc123..."}
 *
 * PC 端「移动设备」对话框扫此码 → 自动填入 IP / Port / Token，
 * 之后所有 HTTP 请求带 Authorization: Bearer <token>。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding

    private val notifPermLauncher = registerForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission()
    ) { /* 用户拒绝也不强求，FgService 通知不会展示但服务仍可跑 */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.btnOpenSettings.setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        binding.btnStartHttp.setOnClickListener {
            ensureNotificationPermission()
            startForegroundService(QianniuForegroundService.startIntent(this))
            // HttpServer 启动是异步的，但通知和按钮态可以先变；400ms 后刷新 UI
            window.decorView.postDelayed({ refreshAll() }, 400)
        }

        binding.btnStopHttp.setOnClickListener {
            startService(QianniuForegroundService.stopIntent(this))
            window.decorView.postDelayed({ refreshAll() }, 400)
        }

        binding.btnResetToken.setOnClickListener {
            PairingAuth.resetToken(this)
            Toast.makeText(this, R.string.toast_token_reset, Toast.LENGTH_SHORT).show()
            refreshQrCode()  // token 变了，二维码也变
        }
    }

    override fun onResume() {
        super.onResume()
        refreshAll()
    }

    private fun refreshAll() {
        refreshAccessibilityStatus()
        refreshHttpStatus()
        refreshQrCode()
    }

    // ── 无障碍 ─────────────────────────────────────────────────

    private fun refreshAccessibilityStatus() {
        val enabled = isAccessibilityServiceEnabled()
        binding.tvStatus.setText(
            if (enabled) R.string.status_enabled else R.string.status_disabled
        )
    }

    private fun isAccessibilityServiceEnabled(): Boolean {
        val am = getSystemService(ACCESSIBILITY_SERVICE) as AccessibilityManager
        val expectedId = "$packageName/.QianniuAccessibilityService"
        return am.getEnabledAccessibilityServiceList(
            android.accessibilityservice.AccessibilityServiceInfo.FEEDBACK_GENERIC
        ).any { it.id == expectedId || it.id.endsWith(expectedId) }
    }

    // ── HTTP 服务 ──────────────────────────────────────────────

    private fun refreshHttpStatus() {
        val running = HttpServer.isRunning()
        binding.tvHttpStatus.setText(
            if (running) R.string.http_status_running else R.string.http_status_stopped
        )
        val ip = NetworkUtil.getWifiIpAddress(this)
        binding.tvIpPort.text = "http://$ip:${HttpServer.PORT}"
    }

    // ── 二维码 ─────────────────────────────────────────────────

    private fun refreshQrCode() {
        val ip = NetworkUtil.getWifiIpAddress(this)
        val token = PairingAuth.getOrCreateToken(this)
        // 简化 JSON 拼装：无嵌套不需要专门的 JSON 库
        val payload = """{"ip":"$ip","port":${HttpServer.PORT},"token":"$token"}"""
        try {
            val bmp = QrCodeUtil.generate(payload, sizePx = 720)
            binding.ivQr.setImageBitmap(bmp)
        } catch (t: Throwable) {
            Toast.makeText(this, "二维码生成失败：${t.message}", Toast.LENGTH_LONG).show()
        }
    }

    // ── Android 13+ 通知权限（前台服务的通知图标需要）────────

    private fun ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        if (ContextCompat.checkSelfPermission(
                this, Manifest.permission.POST_NOTIFICATIONS
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            notifPermLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }
}
