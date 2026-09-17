package `in`.openalgo.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import `in`.openalgo.ui.components.MonetraBar
import `in`.openalgo.ui.components.MonetraPill
import `in`.openalgo.ui.components.PastelCategoryTile
import `in`.openalgo.ui.theme.OpenAlgoColors

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DashboardScreen(
    onNavigateToTrading: () -> Unit = {}
) {
    var selectedAccount by remember { mutableStateOf("Investments") }

    Scaffold(
        containerColor = OpenAlgoColors.CanvasLight,
        topBar = {
            TopAppBar(
                title = {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(32.dp)
                                .clip(RoundedCornerShape(10.dp))
                                .background(OpenAlgoColors.BrandBlue),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(text = "⚡", color = Color.White, fontSize = 16.sp)
                        }
                        Text(
                            text = "OpenAlgo",
                            fontWeight = FontWeight.ExtraBold,
                            fontSize = 18.sp,
                            color = OpenAlgoColors.TextPrimary
                        )
                    }
                },
                actions = {
                    Box(
                        modifier = Modifier
                            .size(36.dp)
                            .clip(CircleShape)
                            .background(Color(0xFFE2E8F0)),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(text = "📊", fontSize = 16.sp)
                    }
                    Spacer(modifier = Modifier.width(12.dp))
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = OpenAlgoColors.CanvasLight
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
            // Upper White Squircle Card (Monetra Credit Score & Bar Chart)
            item {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(26.dp))
                        .background(Color.White)
                        .border(1.dp, Color(0x0F000000), RoundedCornerShape(26.dp))
                        .padding(20.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column {
                            Text(
                                text = "Available Balance",
                                fontSize = 12.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = OpenAlgoColors.TextSecondary
                            )
                            Text(
                                text = "$100.00",
                                fontSize = 36.sp,
                                fontWeight = FontWeight.ExtraBold,
                                color = OpenAlgoColors.TextPrimary
                            )
                        }
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(999.dp))
                                .background(OpenAlgoColors.ProfitGreenBg)
                                .padding(horizontal = 10.dp, vertical = 4.dp)
                        ) {
                            Text(
                                text = "▲ 15.4%",
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Bold,
                                color = OpenAlgoColors.ProfitGreen
                            )
                        }
                    }

                    // Bar Chart
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(130.dp)
                            .padding(vertical = 12.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.Bottom
                    ) {
                        MonetraBar(heightPct = 0.65f, label = "Jan")
                        MonetraBar(heightPct = 0.40f, label = "Feb")
                        MonetraBar(heightPct = 0.50f, label = "Mar", isActive = true)
                        MonetraBar(heightPct = 0.85f, label = "Apr")
                        MonetraBar(heightPct = 0.95f, label = "May")
                        MonetraBar(heightPct = 0.75f, label = "Jun")
                        MonetraBar(heightPct = 0.35f, label = "Jul")
                    }

                    Divider(color = Color(0x10000000), thickness = 1.dp)

                    // View Credit Report Link
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { }
                            .padding(top = 14.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text(text = "📊", fontSize = 14.sp)
                            Text(
                                text = "View Performance Report",
                                fontSize = 13.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = OpenAlgoColors.TextPrimary
                            )
                        }
                        Text(text = "›", fontSize = 18.sp, color = OpenAlgoColors.TextSecondary)
                    }

                    // Account Pills (Checking / Savings / Investments)
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 16.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        listOf("Checking", "Savings", "Investments").forEach { tab ->
                            MonetraPill(
                                text = tab,
                                isSelected = selectedAccount == tab,
                                onClick = { selectedAccount = tab }
                            )
                        }
                    }
                }
            }

            // Dark Charcoal Execution Scheduler Strip
            item {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(26.dp))
                        .background(OpenAlgoColors.CharcoalSheet)
                        .padding(20.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column {
                            Text(
                                text = "EXECUTION SCHEDULER",
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Bold,
                                color = Color(0xFF94A3B8)
                            )
                            Text(
                                text = "2 active orders running",
                                fontSize = 15.sp,
                                fontWeight = FontWeight.Bold,
                                color = Color.White
                            )
                        }

                        Button(
                            onClick = onNavigateToTrading,
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0x25FFFFFF)),
                            shape = RoundedCornerShape(999.dp),
                            contentPadding = PaddingValues(horizontal = 14.dp, vertical = 6.dp)
                        ) {
                            Text(text = "+ Fast Order", fontSize = 12.sp, color = Color.White)
                        }
                    }

                    Spacer(modifier = Modifier.height(16.dp))

                    // Horizontal Week Calendar Strip
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        listOf(
                            Triple("M", "9", false),
                            Triple("T", "10", true),
                            Triple("W", "11", false),
                            Triple("T", "12", false),
                            Triple("F", "13", false),
                            Triple("S", "14", false),
                            Triple("S", "15", false)
                        ).forEach { (day, date, isActive) ->
                            Column(
                                modifier = Modifier
                                    .clip(RoundedCornerShape(16.dp))
                                    .background(if (isActive) Color.White else Color.Transparent)
                                    .padding(horizontal = 10.dp, vertical = 8.dp),
                                horizontalAlignment = Alignment.CenterHorizontally
                            ) {
                                Text(
                                    text = day,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Medium,
                                    color = if (isActive) Color.Black else Color(0xFF94A3B8)
                                )
                                Text(
                                    text = date,
                                    fontSize = 14.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = if (isActive) Color.Black else Color.White
                                )
                            }
                        }
                    }
                }
            }

            // Category Review and Positions List
            item {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        text = "Active Positions",
                        fontWeight = FontWeight.Bold,
                        fontSize = 16.sp,
                        color = OpenAlgoColors.TextPrimary
                    )

                    PastelCategoryTile(
                        title = "BTCUSDT Perpetual",
                        subtitle = "Binance Demo • 10x Long",
                        amount = "+$18.40",
                        isPositive = true,
                        iconBg = OpenAlgoColors.ProfitGreenBg,
                        iconColor = OpenAlgoColors.ProfitGreen
                    )

                    PastelCategoryTile(
                        title = "ETHUSDT Perpetual",
                        subtitle = "Binance Demo • 5x Short",
                        amount = "-$3.20",
                        isPositive = false,
                        iconBg = OpenAlgoColors.LossRoseBg,
                        iconColor = OpenAlgoColors.LossRose
                    )
                }
            }

            item {
                Spacer(modifier = Modifier.height(30.dp))
            }
        }
    }
}
