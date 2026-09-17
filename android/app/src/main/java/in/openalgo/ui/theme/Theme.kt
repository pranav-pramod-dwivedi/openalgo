package `in`.openalgo.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

private val MonetraColorScheme = lightColorScheme(
    primary = OpenAlgoColors.BrandBlue,
    onPrimary = OpenAlgoColors.TextWhite,
    background = OpenAlgoColors.CanvasLight,
    surface = OpenAlgoColors.CardWhite,
    onSurface = OpenAlgoColors.TextPrimary,
    outline = OpenAlgoColors.BorderSubtle,
    error = OpenAlgoColors.LossRose
)

@Composable
fun OpenAlgoTheme(
    content: @Composable () -> Unit
) {
    MaterialTheme(
        colorScheme = MonetraColorScheme,
        typography = OpenAlgoTypography,
        content = content
    )
}
