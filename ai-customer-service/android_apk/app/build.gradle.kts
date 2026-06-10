plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    // Phase 2：开启 kotlinx.serialization（让 @Serializable data class 自动生成序列化代码）
    id("org.jetbrains.kotlin.plugin.serialization")
}

android {
    namespace = "com.aiworkbench.qianniu_agent"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.aiworkbench.qianniu_agent"
        minSdk = 24            // Android 7.0 +（覆盖 99% 主流手机）
        targetSdk = 34          // Android 14
        versionCode = 1
        versionName = "1.5.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
        debug {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        viewBinding = true
    }
}

dependencies {
    // ── AndroidX 基础 ──────────────────────────────────────────────────
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")

    // 协程（无障碍事件处理 + Ktor 协程化）
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")

    // ── Phase 2：JSON 序列化（API 请求/响应）─────────────────────────
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.2")

    // ── Phase 2：Ktor embedded HTTP server（端口 8765 绑 0.0.0.0）─────
    // 2.3.x 与 Kotlin 1.9.22 / coroutines 1.7.x 配套稳定
    val ktorVersion = "2.3.7"
    implementation("io.ktor:ktor-server-core:$ktorVersion")
    implementation("io.ktor:ktor-server-netty:$ktorVersion")
    implementation("io.ktor:ktor-server-content-negotiation:$ktorVersion")
    implementation("io.ktor:ktor-serialization-kotlinx-json:$ktorVersion")
    implementation("io.ktor:ktor-server-auth:$ktorVersion")
    implementation("io.ktor:ktor-server-status-pages:$ktorVersion")
    implementation("io.ktor:ktor-server-cors:$ktorVersion")
    implementation("io.ktor:ktor-server-call-logging:$ktorVersion")

    // ── Phase 2：二维码生成（MainActivity 显示配对令牌）──────────────
    // 用 ZXing core（轻量，不带 Activity / 摄像头模块）
    implementation("com.google.zxing:core:3.5.2")
}
