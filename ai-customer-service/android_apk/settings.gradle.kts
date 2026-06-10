// 仅使用官方源（google / mavenCentral / gradlePluginPortal）。
// 中国大陆网络拉不动 → 全局开代理（VPN / Clash 等）后再做 Gradle Sync。
// 镜像方案（阿里云/清华/腾讯）已知在 AGP 8.2.2 + Kotlin 1.9.22 下不稳定，已移除。

pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "QianniuAgent"
include(":app")
