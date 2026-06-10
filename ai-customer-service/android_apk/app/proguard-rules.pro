# Add project-specific ProGuard rules here.
# 阶段 1 暂无自定义规则，build.gradle.kts 中 isMinifyEnabled=false 时此文件不生效。
# 阶段 5 打 release 包前如果开启 minify，需保留：
#   - AccessibilityService 子类（系统通过反射实例化）
#   - Ktor 路由处理器（阶段 2）
