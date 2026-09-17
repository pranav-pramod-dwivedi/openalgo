package `in`.openalgo.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * OpenAlgo Monetra Luxe Fintech Color Palette
 * Replicated directly from the Monetra mobile app design:
 * - Crisp light canvas (#F4F6F9) + Dark Charcoal Bottom Sheet (#18191D)
 * - Monetra Royal Blue / Indigo primary (#3B82F6 / #2563EB)
 * - Soft pastel category badges (Pink, Blue, Green, Purple)
 */
object OpenAlgoColors {
    // Monetra Surfaces
    val CanvasLight = Color(0xFFF4F6F9)        // Crisp serene background
    val CardWhite = Color(0xFFFFFFFF)          // Upper squircle card
    val CharcoalSheet = Color(0xFF18191D)      // Deep charcoal bottom container
    val CharcoalCard = Color(0xFF22242B)       // Elevated tile inside charcoal container
    val BorderSubtle = Color(0x12000000)       // 7% black border

    // Monetra Brand Accent
    val BrandBlue = Color(0xFF3B82F6)          // Monetra vibrant electric blue
    val BrandBlueDark = Color(0xFF1D4ED8)
    val BrandBlueSoft = Color(0xFFEFF6FF)

    // Signals
    val ProfitGreen = Color(0xFF10B981)        // Emerald profit
    val ProfitGreenBg = Color(0xFFECFDF5)
    val LossRose = Color(0xFFF43F5E)           // Coral loss
    val LossRoseBg = Color(0xFFFDF2F8)

    // Soft Pastel Category Tiles
    val PastelPinkBg = Color(0xFFFDF2F8)
    val PastelPinkIcon = Color(0xFFEC4899)
    val PastelPurpleBg = Color(0xFFF3E8FF)
    val PastelPurpleIcon = Color(0xFFA855F7)

    // Typography
    val TextPrimary = Color(0xFF13151A)        // Deep charcoal text
    val TextSecondary = Color(0xFF64748B)      // Slate muted text
    val TextWhite = Color(0xFFFFFFFF)
    val TextWhiteMuted = Color(0xFF94A3B8)
}
