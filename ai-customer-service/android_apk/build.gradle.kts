// Top-level build file
plugins {
    id("com.android.application") version "8.2.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.22" apply false
    // Phase 2：JSON 序列化（用于 Ktor API 请求/响应序列化）
    id("org.jetbrains.kotlin.plugin.serialization") version "1.9.22" apply false
}
