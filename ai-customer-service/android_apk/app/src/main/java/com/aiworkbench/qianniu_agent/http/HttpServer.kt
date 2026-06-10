package com.aiworkbench.qianniu_agent.http

import android.content.Context
import android.util.DisplayMetrics
import android.util.Log
import android.view.WindowManager
import com.aiworkbench.qianniu_agent.QianniuAccessibilityService
import com.aiworkbench.qianniu_agent.auth.PairingAuth
import com.aiworkbench.qianniu_agent.humanize.HumanBehavior
import com.aiworkbench.qianniu_agent.qianniu.Locators
import com.aiworkbench.qianniu_agent.qianniu.MessageReader
import com.aiworkbench.qianniu_agent.qianniu.MessageSender
import com.aiworkbench.qianniu_agent.qianniu.QianniuMessage
import com.aiworkbench.qianniu_agent.qianniu.QianniuSession
import com.aiworkbench.qianniu_agent.qianniu.SessionListReader
import io.ktor.http.HttpMethod
import io.ktor.http.HttpStatusCode
import io.ktor.serialization.kotlinx.json.json
import io.ktor.server.application.ApplicationCallPipeline
import io.ktor.server.application.call
import io.ktor.server.application.install
import io.ktor.server.engine.embeddedServer
import io.ktor.server.netty.Netty
import io.ktor.server.netty.NettyApplicationEngine
import io.ktor.server.plugins.callloging.CallLogging
import io.ktor.server.plugins.contentnegotiation.ContentNegotiation
import io.ktor.server.plugins.cors.routing.CORS
import io.ktor.server.plugins.statuspages.StatusPages
import io.ktor.server.request.receive
import io.ktor.server.response.respond
import io.ktor.server.routing.get
import io.ktor.server.routing.post
import io.ktor.server.routing.route
import io.ktor.server.routing.routing
import kotlinx.coroutines.delay
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Ktor embedded HTTP server。
 *
 *   - 端口 8765 绑 0.0.0.0（局域网内可访问）
 *   - 所有 /api/* endpoint 必须带 Authorization: Bearer <token>
 *   - /health 不鉴权（用于 mDNS 发现后的连通性检测）
 *
 * 6 个核心 endpoint：
 *   GET  /health                 → 不鉴权，返回版本 + uptime
 *   GET  /api/sessions           → SessionListReader 读会话列表
 *   GET  /api/messages?n=5       → MessageReader 读当前聊天页消息
 *   POST /api/switch             → 点会话条进入聊天页（body: {name:"买家昵称"}）
 *   POST /api/send               → MessageSender 发消息（body: {text:"..."}）+ 自动加拟人化延迟
 *   GET  /api/current_anchor     → 返回当前在哪个会话（页面标题）
 *
 * 失败统一返回 ApiError（StatusPages 兜底）。
 */
object HttpServer {

    private const val TAG = "QianniuAgent.Http"
    const val PORT = 8765

    @Volatile
    private var engine: NettyApplicationEngine? = null
    @Volatile
    private var startedAtMs: Long = 0L

    @Synchronized
    fun start(context: Context) {
        if (engine != null) {
            Log.w(TAG, "HttpServer.start 跳过：已在运行")
            return
        }
        val appContext = context.applicationContext
        val e = embeddedServer(Netty, port = PORT, host = "0.0.0.0") {
            install(ContentNegotiation) {
                json(Json {
                    ignoreUnknownKeys = true
                    encodeDefaults = true
                })
            }
            install(CORS) {
                anyHost()
                allowHeader("Authorization")
                allowHeader("Content-Type")
                allowMethod(HttpMethod.Get)
                allowMethod(HttpMethod.Post)
            }
            install(CallLogging)
            install(StatusPages) {
                exception<Throwable> { call, cause ->
                    Log.e(TAG, "请求异常: ${cause.message}", cause)
                    call.respond(
                        HttpStatusCode.InternalServerError,
                        ApiError("internal_error", cause.message ?: "unknown")
                    )
                }
            }

            routing {
                // ── 不鉴权 ──
                get("/health") {
                    call.respond(
                        HealthResp(
                            ok = true,
                            version = "1.5.0",
                            uptimeMs = System.currentTimeMillis() - startedAtMs,
                            accessibilityConnected = QianniuAccessibilityService.instance != null,
                        )
                    )
                }

                // ── 需鉴权 /api/* ──
                route("/api") {
                    intercept(ApplicationCallPipeline.Plugins) {
                        val header = call.request.headers["Authorization"]
                        val token = header?.removePrefix("Bearer ")?.trim()
                        if (!PairingAuth.verifyToken(appContext, token)) {
                            call.respond(
                                HttpStatusCode.Unauthorized,
                                ApiError("unauthorized", "missing or invalid bearer token")
                            )
                            finish()
                            return@intercept
                        }
                    }

                    get("/sessions") {
                        val svc = QianniuAccessibilityService.instance
                        if (svc == null) {
                            call.respond(
                                HttpStatusCode.ServiceUnavailable,
                                ApiError(
                                    "accessibility_not_connected",
                                    "请去系统设置 → 无障碍 → 启用「千牛接待助手」"
                                )
                            )
                            return@get
                        }
                        val list = SessionListReader.read(svc.rootInActiveWindow)
                        call.respond(SessionsResp(sessions = list.map { it.toDto() }))
                    }

                    get("/messages") {
                        val n = call.request.queryParameters["n"]?.toIntOrNull() ?: 5
                        val svc = QianniuAccessibilityService.instance
                            ?: return@get call.respond(
                                HttpStatusCode.ServiceUnavailable,
                                ApiError("accessibility_not_connected", "")
                            )
                        val width = getScreenWidthPx(appContext)
                        val msgs = MessageReader.read(svc.rootInActiveWindow, width, n)
                        call.respond(MessagesResp(messages = msgs.map { it.toDto() }))
                    }

                    post("/switch") {
                        val req = call.receive<SwitchReq>()
                        val svc = QianniuAccessibilityService.instance
                            ?: return@post call.respond(
                                HttpStatusCode.ServiceUnavailable,
                                ApiError("accessibility_not_connected", "")
                            )
                        val sessions = SessionListReader.read(svc.rootInActiveWindow)
                        val target = sessions.firstOrNull { it.name == req.name }
                            ?: return@post call.respond(
                                HttpStatusCode.NotFound,
                                ApiError("session_not_found", "name=${req.name}")
                            )
                        val cx = (target.boundsLeft + target.boundsRight) / 2
                        val cy = (target.boundsTop + target.boundsBottom) / 2
                        val ok = MessageSender.tapAt(svc, cx, cy)
                        call.respond(SwitchResp(ok = ok, x = cx, y = cy))
                    }

                    post("/send") {
                        val req = call.receive<SendReq>()
                        if (req.text.isBlank()) {
                            return@post call.respond(
                                HttpStatusCode.BadRequest,
                                ApiError("empty_text", "")
                            )
                        }
                        // 深夜降级：直接拒绝
                        if (HumanBehavior.isInQuietHours()) {
                            return@post call.respond(
                                HttpStatusCode.ServiceUnavailable,
                                ApiError("quiet_hours", "now is in 01:00-07:00, send blocked")
                            )
                        }
                        // 拟人化等待（协程 delay，不阻塞 Netty IO 线程）
                        val waitMs = if (req.skipHumanize) 0L
                                     else HumanBehavior.computeReplyDelayMs(req.text)
                        if (waitMs > 0) delay(waitMs)

                        val svc = QianniuAccessibilityService.instance
                            ?: return@post call.respond(
                                HttpStatusCode.ServiceUnavailable,
                                ApiError("accessibility_not_connected", "")
                            )
                        val sent = MessageSender.send(svc, req.text)
                        call.respond(SendResp(ok = sent, waitedMs = waitMs))
                    }

                    get("/current_anchor") {
                        val svc = QianniuAccessibilityService.instance
                            ?: return@get call.respond(
                                HttpStatusCode.ServiceUnavailable,
                                ApiError("accessibility_not_connected", "")
                            )
                        val root = svc.rootInActiveWindow
                        val packageName = root?.packageName?.toString() ?: "unknown"
                        val title = Locators.CHAT_TITLE.firstNotNullOfOrNull { id ->
                            root?.findAccessibilityNodeInfosByViewId(id)
                                ?.firstOrNull()?.text?.toString()?.trim()
                        } ?: ""
                        call.respond(AnchorResp(packageName = packageName, chatTitle = title))
                    }
                }
            }
        }
        startedAtMs = System.currentTimeMillis()
        e.start(wait = false)
        engine = e
        Log.i(TAG, "═══ Ktor HttpServer 已启动 :$PORT ═══")
    }

    @Synchronized
    fun stop() {
        val e = engine ?: return
        try {
            e.stop(gracePeriodMillis = 500, timeoutMillis = 1500)
            Log.i(TAG, "HttpServer 已停止")
        } catch (t: Throwable) {
            Log.w(TAG, "HttpServer.stop 异常: ${t.message}")
        } finally {
            engine = null
        }
    }

    fun isRunning(): Boolean = engine != null

    private fun getScreenWidthPx(ctx: Context): Int {
        val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        @Suppress("DEPRECATION")
        val metrics = DisplayMetrics().also { wm.defaultDisplay.getRealMetrics(it) }
        return metrics.widthPixels
    }
}

// ── DTOs（序列化到 JSON 的纯数据类）─────────────────────────────────

@Serializable
data class ApiError(val error: String, val reason: String)

@Serializable
data class HealthResp(
    val ok: Boolean,
    val version: String,
    val uptimeMs: Long,
    val accessibilityConnected: Boolean,
)

@Serializable
data class SessionDto(
    val name: String,
    val unreadCount: Int,
    val previewText: String? = null,
    val boundsLeft: Int,
    val boundsTop: Int,
    val boundsRight: Int,
    val boundsBottom: Int,
)

@Serializable
data class SessionsResp(val sessions: List<SessionDto>)

@Serializable
data class MessageDto(
    val text: String,
    val side: String,        // "LEFT" / "RIGHT" / "UNKNOWN"
    val capturedAt: Long,
)

@Serializable
data class MessagesResp(val messages: List<MessageDto>)

@Serializable
data class SwitchReq(val name: String)

@Serializable
data class SwitchResp(val ok: Boolean, val x: Int, val y: Int)

@Serializable
data class SendReq(val text: String, val skipHumanize: Boolean = false)

@Serializable
data class SendResp(val ok: Boolean, val waitedMs: Long)

@Serializable
data class AnchorResp(val packageName: String, val chatTitle: String)

// ── 业务对象 → DTO 的扩展函数 ─────────────────────────────────────

private fun QianniuSession.toDto() = SessionDto(
    name = name,
    unreadCount = unreadCount,
    previewText = previewText,
    boundsLeft = boundsLeft,
    boundsTop = boundsTop,
    boundsRight = boundsRight,
    boundsBottom = boundsBottom,
)

private fun QianniuMessage.toDto() = MessageDto(
    text = text,
    side = side.name,
    capturedAt = capturedAt,
)
