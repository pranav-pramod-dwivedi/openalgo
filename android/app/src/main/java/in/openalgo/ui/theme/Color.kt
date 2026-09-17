package `in`.openalgo.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * OpenAlgo High-Density Financial Terminal Color Palette
 * Obsidian dark surfaces, hairline borders, and WCAG AA high-contrast financial semantics.
 */
object OpenAlgoColors {
    // Obsidian Dark Surface Hierarchy
    val ObsidianDark = Color(0xFF090A0F)
    val SurfaceDark = Color(0xFF12151F)
    val SurfaceCard = Color(0xFF171B27)
    val SurfaceElevated = Color(0xFF1E2333)
    val HairlineBorder = Color(0x1FFFFFFF) // 12% white hairline border

    // Architectural Light Surfaces
    val PaperWhite = Color(0xFFFCFCFD)
    val SurfaceLight = Color(0xFFF4F5F7)
    val SurfaceCardLight = Color(0xFFFFFFFF)
    val HairlineBorderLight = Color(0x14000000)

    // Financial Semantic Accents
    val ProfitGreen = Color(0xFF10B981)       // Emerald Green for Long / Positive P&L
    val ProfitGreenBg = Color(0x1A10B981)     // 10% alpha container
    val ProfitGreenBorder = Color(0x3310B981) // 20% alpha border

    val LossCrimson = Color(0xFFF43F5E)       // Rose Crimson for Short / Loss P&L
    val LossCrimsonBg = Color(0x1AF43F5E)
    val LossCrimsonBorder = Color(0x33F43F5E)

    val AmberWarning = Color(0xFFF59E0B)      // Binance Gold / Caution
    val AmberWarningBg = Color(0x1AF59E0B)
    val AmberWarningBorder = Color(0x33F59E0B)

    val CyanData = Color(0xFF06B6D4)          // Feed / Margin / Stats
    val CyanDataBg = Color(0x1A06B6D4)

    // Text & Muted
    val TextPrimaryDark = Color(0xFFF8FAFC)
    val TextSecondaryDark = Color(0xFF94A3B8)
    val TextTertiaryDark = Color(0xFF64748B)

    val TextPrimaryLight = Color(0xFF0F172A)
    val TextSecondaryLight = Color(0xFF475569)
    val TextTertiaryLight = Color(0xFF94A3B8)
}
