package com.aiworkbench.qianniu_agent.util

import android.graphics.Bitmap
import android.graphics.Color
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel

/**
 * 二维码生成（zxing-core，无 Activity 依赖）。
 *
 * 用法：
 *   val payload = """{"ip":"192.168.1.42","port":8765,"token":"xxxx"}"""
 *   val bmp = QrCodeUtil.generate(payload, sizePx = 512)
 *   imageView.setImageBitmap(bmp)
 */
object QrCodeUtil {

    /**
     * @param content    要编码的字符串（建议 < 1KB；token 32 字节 + IP/port JSON ~ 100 字节，远低于上限）
     * @param sizePx     输出位图边长（正方形）
     * @param margin     四周静默区模块数（zxing 默认 4，可设 1 让二维码占满）
     * @param errorLevel 容错级别：L 7% / M 15% / Q 25% / H 30%
     */
    fun generate(
        content: String,
        sizePx: Int = 512,
        margin: Int = 1,
        errorLevel: ErrorCorrectionLevel = ErrorCorrectionLevel.M,
    ): Bitmap {
        require(content.isNotEmpty()) { "QR content empty" }
        require(sizePx > 0) { "QR sizePx must be > 0" }

        val hints = mapOf(
            EncodeHintType.CHARACTER_SET to "UTF-8",
            EncodeHintType.MARGIN to margin,
            EncodeHintType.ERROR_CORRECTION to errorLevel,
        )

        val writer = QRCodeWriter()
        val matrix = writer.encode(content, BarcodeFormat.QR_CODE, sizePx, sizePx, hints)

        val w = matrix.width
        val h = matrix.height
        val pixels = IntArray(w * h)
        for (y in 0 until h) {
            val row = y * w
            for (x in 0 until w) {
                pixels[row + x] = if (matrix[x, y]) Color.BLACK else Color.WHITE
            }
        }
        val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        bmp.setPixels(pixels, 0, w, 0, 0, w, h)
        return bmp
    }
}
