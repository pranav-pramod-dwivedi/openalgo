package `in`.openalgo.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

private val DarkColorScheme = darkColorScheme(
    primary = OpenAlgoColors.TextPrimaryDark,
    onPrimary = OpenAlgoColors.ObsidianDark,
    background = OpenAlgoColors.ObsidianDark,
    surface = OpenAlgoColors.SurfaceDark,
    onSurface = OpenAlgoColors.TextPrimaryDark,
    outline = OpenAlgoColors.HairlineBorder,
    error = OpenAlgoColors.LossCrimson
)

private val LightColorScheme = lightColorScheme(
    primary = OpenAlgoColors.TextPrimaryLight,
    onPrimary = OpenAlgoColors.PaperWhite,
    background = OpenAlgoColors.PaperWhite,
    surface = OpenAlgoColors.SurfaceLight,
    onSurface = OpenAlgoColors.TextPrimaryLight,
    outline = OpenAlgoColors.HairlineBorderLight,
    error = OpenAlgoColors.LossCrimson
)

@Composable
fun OpenAlgoTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit
) {
    val colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme

    MaterialTheme(
        colorScheme = colorScheme,
        typography = OpenAlgoTypography,
        content = content
    )
}
