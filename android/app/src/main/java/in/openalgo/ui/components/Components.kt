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
 * Monetra Segmented Pill Button
 */
@Composable
fun MonetraPill(
    text: String,
    isSelected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    val bgColor = if (isSelected) Color(0xFF18191D) else Color(0xFFEAEBED)
    val textColor = if (isSelected) Color(0xFFFFFFFF) else Color(0xFF64748B)

    Box(
        modifier = modifier
            .clip(RoundedCornerShape(999.dp))
            .background(bgColor)
            .clickable { onClick() }
            .padding(horizontal = 14.dp, vertical = 7.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = text,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
            color = textColor
        )
    }
}

/**
 * Pastel Category Tile with Icon
 */
@Composable
fun PastelCategoryTile(
    title: String,
    subtitle: String,
    amount: String,
    isPositive: Boolean = true,
    iconBg: Color = OpenAlgoColors.PastelPinkBg,
    iconColor: Color = OpenAlgoColors.PastelPinkIcon
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(18.dp))
            .background(Color.White)
            .border(1.dp, Color(0x0F000000), RoundedCornerShape(18.dp))
            .padding(14.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Box(
                modifier = Modifier
                    .size(42.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(iconBg),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = if (isPositive) "▲" else "🛒",
                    fontSize = 16.sp,
                    color = iconColor
                )
            }
            Column {
                Text(
                    text = title,
                    fontWeight = FontWeight.Bold,
                    fontSize = 14.sp,
                    color = OpenAlgoColors.TextPrimary
                )
                Text(
                    text = subtitle,
                    fontSize = 11.sp,
                    color = OpenAlgoColors.TextSecondary
                )
            }
        }

        Text(
            text = amount,
            fontWeight = FontWeight.Bold,
            fontSize = 14.sp,
            fontFamily = FontFamily.Monospace,
            color = if (isPositive) OpenAlgoColors.ProfitGreen else OpenAlgoColors.LossRose
        )
    }
}

/**
 * Monetra Rounded Bar Indicator
 */
@Composable
fun MonetraBar(
    heightPct: Float,
    label: String,
    isActive: Boolean = false,
    modifier: Modifier = Modifier
) {
    val barColor = if (isActive) OpenAlgoColors.BrandBlue else Color(0xFFE2E8F0)

    Column(
        modifier = modifier.fillMaxHeight(),
        horizontalAlignment = Alignment.CenterAlignmentLine(Alignment.CenterHorizontally).let { Alignment.CenterHorizontally },
        verticalArrangement = Arrangement.Bottom
    ) {
        Box(
            modifier = Modifier
                .width(28.dp)
                .fillMaxHeight(heightPct)
                .clip(RoundedCornerShape(10.dp))
                .background(barColor)
        )
        Spacer(modifier = Modifier.height(6.dp))
        Text(
            text = label,
            fontSize = 11.sp,
            fontWeight = if (isActive) FontWeight.Bold else FontWeight.Normal,
            color = if (isActive) OpenAlgoColors.TextPrimary else OpenAlgoColors.TextSecondary
        )
    }
}
