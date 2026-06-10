package com.aiworkbench.qianniu_agent.util

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkAddress
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.os.Build
import java.net.Inet4Address
import java.net.NetworkInterface

/**
 * 获取本机在局域网内可被访问的 IPv4。
 *
 * 优先级：
 *   1. WiFi（卖家正常用 WiFi）
 *   2. 任意非回环 IPv4 网卡（如 USB tethering、虚拟网卡）
 *   3. fallback "127.0.0.1"（永远不会失败返回，用于二维码兜底）
 */
object NetworkUtil {

    fun getWifiIpAddress(ctx: Context): String {
        // 1. ConnectivityManager（Android M+ 优先）
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            try {
                val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
                val active = cm.activeNetwork
                if (active != null) {
                    val caps = cm.getNetworkCapabilities(active)
                    if (caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true) {
                        val lp = cm.getLinkProperties(active)
                        val ipv4 = lp?.linkAddresses?.firstOrNull { addr: LinkAddress ->
                            addr.address is Inet4Address &&
                                    !addr.address.isLoopbackAddress
                        }?.address?.hostAddress
                        if (!ipv4.isNullOrBlank()) return ipv4
                    }
                }
            } catch (_: Throwable) { /* 跌穿到下一种方法 */ }
        }

        // 2. WifiManager（兼容老版本）
        try {
            val wm = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            @Suppress("DEPRECATION")
            val raw = wm.connectionInfo?.ipAddress ?: 0
            if (raw != 0) {
                return String.format(
                    "%d.%d.%d.%d",
                    raw and 0xff,
                    (raw shr 8) and 0xff,
                    (raw shr 16) and 0xff,
                    (raw shr 24) and 0xff
                )
            }
        } catch (_: Throwable) {}

        // 3. 枚举所有网卡找非回环 IPv4
        try {
            val ifaces = NetworkInterface.getNetworkInterfaces() ?: return "127.0.0.1"
            for (iface in ifaces) {
                if (!iface.isUp || iface.isLoopback || iface.isVirtual) continue
                for (addr in iface.inetAddresses) {
                    if (addr is Inet4Address && !addr.isLoopbackAddress) {
                        return addr.hostAddress ?: continue
                    }
                }
            }
        } catch (_: Throwable) {}

        return "127.0.0.1"
    }
}
