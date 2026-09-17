package `in`.openalgo.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * OpenAlgo Apple Human Interface Guidelines (HIG) Color Palette
 * Follows official iOS and macOS system colors:
 * - System Backgrounds: Light (#F2F2F7 grouped) & Dark (#000000 true black, #1C1C1E card)
 * - Apple System Blue: #0071E3 (light) / #0A84FF (dark)
 * - Apple System Green: #34C759 / #30D158
 * - Apple System Red: #FF3B30 / #FF453A
 */
object OpenAlgoColors {
    // Apple System Surfaces (Light)
    val CanvasLight = Color(0xFFF2F2F7)        // Apple grouped background
    val CardWhite = Color(0xFFFFFFFF)          // Apple secondary grouped background
    val BorderSubtle = Color(0x14000000)       // Apple light separator (8% black)

    // Apple System Surfaces (Dark)
    val PureBlack = Color(0xFF000000)          // Apple iOS pure black
    val CharcoalCard = Color(0xFF1C1C1E)       // Apple dark secondary grouped background
    val ElevatedSurface = Color(0xFF2C2C2E)    // Apple dark tertiary grouped background
    val BorderDark = Color(0x26FFFFFF)         // Apple dark separator (15% white)

    // Apple Brand Accent (System Blue)
    val SystemBlue = Color(0xFF0071E3)         // Apple blue
    val SystemBlueDark = Color(0xFF0A84FF)     // Apple dark mode blue

    // Apple Semantic Signals
    val SystemGreen = Color(0xFF34C759)        // Apple profit green
    val SystemGreenDark = Color(0xFF30D158)
    val SystemGreenBg = Color(0x1F34C759)

    val SystemRed = Color(0xFFFF3B30)          // Apple loss red
    val SystemRedDark = Color(0xFFFF453A)
    val SystemRedBg = Color(0x1FFF3B30)

    val SystemOrange = Color(0xFFFF9500)       // Apple warning orange

    // Apple Typography Label Colors
    val LabelPrimaryLight = Color(0xFF1D1D1F)  // Apple primary label (light)
    val LabelSecondaryLight = Color(0xFF86868B)// Apple secondary label (light)
    val LabelPrimaryDark = Color(0xFFF5F5F7)   // Apple primary label (dark)
    val LabelSecondaryDark = Color(0xFF8E8E93) // Apple secondary label (dark)
}
