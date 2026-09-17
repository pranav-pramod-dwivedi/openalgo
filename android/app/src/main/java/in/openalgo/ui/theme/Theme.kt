package `in`.openalgo.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

private val AppleDarkColorScheme = darkColorScheme(
    primary = OpenAlgoColors.SystemBlueDark,
    onPrimary = OpenAlgoColors.LabelPrimaryDark,
    background = OpenAlgoColors.PureBlack,
    surface = OpenAlgoColors.CharcoalCard,
    onSurface = OpenAlgoColors.LabelPrimaryDark,
    outline = OpenAlgoColors.BorderDark,
    error = OpenAlgoColors.SystemRedDark
)

private val AppleLightColorScheme = lightColorScheme(
    primary = OpenAlgoColors.SystemBlue,
    onPrimary = OpenAlgoColors.CardWhite,
    background = OpenAlgoColors.CanvasLight,
    surface = OpenAlgoColors.CardWhite,
    onSurface = OpenAlgoColors.LabelPrimaryLight,
    outline = OpenAlgoColors.BorderSubtle,
    error = OpenAlgoColors.SystemRed
)

@Composable
fun OpenAlgoTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit
) {
    val colorScheme = if (darkTheme) AppleDarkColorScheme else AppleLightColorScheme

    MaterialTheme(
        colorScheme = colorScheme,
        typography = OpenAlgoTypography,
        content = content
    )
}
