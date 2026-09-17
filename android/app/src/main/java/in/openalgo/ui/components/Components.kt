package `in`.openalgo.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import `in`.openalgo.ui.theme.OpenAlgoColors

/**
 * Tactical Terminal Metric Card with hairline border and mono figures
 */
@Composable
fun MetricCard(
    label: String,
    value: String,
    subtext: String,
    modifier: Modifier = Modifier,
    isPositive: Boolean? = null,
    badgeColor: Color = Color.Unspecified
) {
    val valueColor = when (isPositive) {
        true -> OpenAlgoColors.ProfitGreen
        false -> OpenAlgoColors.LossCrimson
        null -> OpenAlgoColors.TextPrimaryDark
    }

    Box(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(OpenAlgoColors.SurfaceCard)
            .border(1.dp, OpenAlgoColors.HairlineBorder, RoundedCornerShape(8.dp))
            .padding(12.dp)
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(
                text = label.uppercase(),
                fontFamily = FontFamily.Monospace,
                fontSize = 10.sp,
                fontWeight = FontWeight.SemiBold,
                color = OpenAlgoColors.TextSecondaryDark,
                letterSpacing = 0.5.sp
            )
            Text(
                text = value,
                fontFamily = FontFamily.Monospace,
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
                color = valueColor
            )
            Text(
                text = subtext,
                fontSize = 11.sp,
                color = if (badgeColor != Color.Unspecified) badgeColor else OpenAlgoColors.TextTertiaryDark
            )
        }
    }
}

/**
 * Directional Side Tag (LONG vs SHORT)
 */
@Composable
fun PositionSideBadge(side: String) {
    val isLong = side.uppercase() == "LONG"
    val bgColor = if (isLong) OpenAlgoColors.ProfitGreenBg else OpenAlgoColors.LossCrimsonBg
    val textColor = if (isLong) OpenAlgoColors.ProfitGreen else OpenAlgoColors.LossCrimson
    val borderColor = if (isLong) OpenAlgoColors.ProfitGreenBorder else OpenAlgoColors.LossCrimsonBorder

    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(4.dp))
            .background(bgColor)
            .border(1.dp, borderColor, RoundedCornerShape(4.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp)
    ) {
        Text(
            text = if (isLong) "▲ LONG" else "▼ SHORT",
            fontFamily = FontFamily.Monospace,
            fontSize = 10.sp,
            fontWeight = FontWeight.Bold,
            color = textColor
        )
    }
}

/**
 * Tactical Segmented Market Selector
 */
@Composable
fun MarketSegmentTab(
    title: String,
    isSelected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier
            .clip(RoundedCornerShape(6.dp))
            .background(if (isSelected) OpenAlgoColors.SurfaceElevated else Color.Transparent)
            .clickable { onClick() }
            .padding(horizontal = 12.dp, vertical = 6.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = title,
            fontSize = 12.sp,
            fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Medium,
            color = if (isSelected) OpenAlgoColors.TextPrimaryDark else OpenAlgoColors.TextSecondaryDark
        )
    }
}

/**
 * Live Engine Status Heartbeat Pill
 */
@Composable
fun LiveStatusPill(status: String = "LIVE ENGINE") {
    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(12.dp))
            .background(OpenAlgoColors.ProfitGreenBg)
            .border(1.dp, OpenAlgoColors.ProfitGreenBorder, RoundedCornerShape(12.dp))
            .padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp)
    ) {
        Box(
            modifier = Modifier
                .size(6.dp)
                .clip(CircleShape)
                .background(OpenAlgoColors.ProfitGreen)
        )
        Text(
            text = status,
            fontFamily = FontFamily.Monospace,
            fontSize = 10.sp,
            fontWeight = FontWeight.Bold,
            color = OpenAlgoColors.ProfitGreen
        )
    }
}
