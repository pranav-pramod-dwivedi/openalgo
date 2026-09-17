package `in`.openalgo.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import `in`.openalgo.ui.components.*
import `in`.openalgo.ui.theme.OpenAlgoColors

data class PositionItem(
    val symbol: String,
    val side: String,
    val amount: Double,
    val entryPrice: Double,
    val markPrice: Double,
    val unrealizedPnl: Double
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DashboardScreen(
    onNavigateToTrading: () -> Unit = {}
) {
    var selectedMarket by remember { mutableStateOf("Binance Demo") }

    val mockPositions = remember {
        listOf(
            PositionItem("BTCUSDT", "LONG", 0.02, 84250.00, 84480.00, 4.60),
            PositionItem("ETHUSDT", "SHORT", 0.50, 2410.50, 2395.20, 7.65)
        )
    }

    Scaffold(
        containerColor = OpenAlgoColors.ObsidianDark,
        topBar = {
            TopAppBar(
                title = {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Text(
                            text = "OpenAlgo",
                            fontWeight = FontWeight.Bold,
                            color = OpenAlgoColors.TextPrimaryDark
                        )
                        LiveStatusPill()
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = OpenAlgoColors.ObsidianDark
                )
            )
        }
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // Market Switcher
            item {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(8.dp))
                        .background(OpenAlgoColors.SurfaceDark)
                        .border(1.dp, OpenAlgoColors.HairlineBorder, RoundedCornerShape(8.dp))
                        .padding(4.dp),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    listOf("🇮🇳 INR", "🌐 USD", "🟡 Binance Demo").forEach { market ->
                        MarketSegmentTab(
                            title = market,
                            isSelected = selectedMarket == market,
                            onClick = { selectedMarket = market },
                            modifier = Modifier.weight(1f)
                        )
                    }
                }
            }

            // Account Equity Banner
            item {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(8.dp))
                        .background(OpenAlgoColors.SurfaceCard)
                        .border(1.dp, OpenAlgoColors.HairlineBorder, RoundedCornerShape(8.dp))
                        .padding(16.dp)
                ) {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = "ACCOUNT EQUITY",
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = OpenAlgoColors.TextSecondaryDark
                            )
                            Text(
                                text = "STARTING: $100.00",
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Bold,
                                color = OpenAlgoColors.AmberWarning
                            )
                        }
                        Text(
                            text = "$100.59",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 32.sp,
                            fontWeight = FontWeight.Bold,
                            color = OpenAlgoColors.TextPrimaryDark
                        )
                        Row(
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = "15k USDC Collateral Excluded",
                                fontSize = 11.sp,
                                color = OpenAlgoColors.ProfitGreen
                            )
                            Text(
                                text = "• HMAC-SHA256 Authenticated",
                                fontSize = 11.sp,
                                color = OpenAlgoColors.TextTertiaryDark
                            )
                        }
                    }
                }
            }

            // 4-Card Telemetry Grid
            item {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        MetricCard(
                            label = "Cash Balance",
                            value = "$100.00",
                            subtext = "Base Capital",
                            modifier = Modifier.weight(1f)
                        )
                        MetricCard(
                            label = "Collateral",
                            value = "$100.00",
                            subtext = "15k Hidden",
                            badgeColor = OpenAlgoColors.AmberWarning,
                            modifier = Modifier.weight(1f)
                        )
                    }
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        MetricCard(
                            label = "Unrealized P&L",
                            value = "+$12.25",
                            subtext = "Live Mark-to-Market",
                            isPositive = true,
                            modifier = Modifier.weight(1f)
                        )
                        MetricCard(
                            label = "Booked P&L",
                            value = "$0.00",
                            subtext = "Session Closed",
                            modifier = Modifier.weight(1f)
                        )
                    }
                }
            }

            // Live Positions Table Section
            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "Live Futures Positions",
                        fontFamily = FontFamily.SansSerif,
                        fontWeight = FontWeight.Bold,
                        fontSize = 16.sp,
                        color = OpenAlgoColors.TextPrimaryDark
                    )
                    Text(
                        text = "${mockPositions.size} Active",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        color = OpenAlgoColors.TextSecondaryDark
                    )
                }
            }

            items(mockPositions) { pos ->
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(8.dp))
                        .background(OpenAlgoColors.SurfaceCard)
                        .border(1.dp, OpenAlgoColors.HairlineBorder, RoundedCornerShape(8.dp))
                        .padding(12.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                Text(
                                    text = pos.symbol,
                                    fontFamily = FontFamily.Monospace,
                                    fontWeight = FontWeight.Bold,
                                    fontSize = 14.sp,
                                    color = OpenAlgoColors.TextPrimaryDark
                                )
                                PositionSideBadge(side = pos.side)
                            }
                            Text(
                                text = "Qty: ${pos.amount} • Entry: $${pos.entryPrice}",
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                                color = OpenAlgoColors.TextTertiaryDark
                            )
                        }
                        Column(
                            horizontalAlignment = Alignment.End,
                            verticalArrangement = Arrangement.spacedBy(4.dp)
                        ) {
                            Text(
                                text = (if (pos.unrealizedPnl >= 0) "+$" else "-$") + "%.2f".format(kotlin.math.abs(pos.unrealizedPnl)),
                                fontFamily = FontFamily.Monospace,
                                fontWeight = FontWeight.Bold,
                                fontSize = 14.sp,
                                color = if (pos.unrealizedPnl >= 0) OpenAlgoColors.ProfitGreen else OpenAlgoColors.LossCrimson
                            )
                            Text(
                                text = "Mark: $${pos.markPrice}",
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                                color = OpenAlgoColors.TextTertiaryDark
                            )
                        }
                    }
                }
            }

            item {
                Spacer(modifier = Modifier.height(24.dp))
            }
        }
    }
}
