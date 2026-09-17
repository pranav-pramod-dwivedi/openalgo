package `in`.openalgo.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
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
import `in`.openalgo.ui.components.LiveStatusPill
import `in`.openalgo.ui.theme.OpenAlgoColors

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TradingScreen(
    onNavigateBack: () -> Unit = {}
) {
    var symbol by remember { mutableStateOf("BTCUSDT") }
    var orderSide by remember { mutableStateOf("BUY") }
    var orderType by remember { mutableStateOf("LIMIT") }
    var quantity by remember { mutableStateOf("0.02") }
    var limitPrice by remember { mutableStateOf("84250.00") }

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
                            text = "Execution Terminal",
                            fontWeight = FontWeight.Bold,
                            color = OpenAlgoColors.TextPrimaryDark
                        )
                        LiveStatusPill(status = "DEMO FEED")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = OpenAlgoColors.ObsidianDark
                )
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // Buy / Sell Toggle
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(8.dp))
                    .background(OpenAlgoColors.SurfaceDark)
                    .border(1.dp, OpenAlgoColors.HairlineBorder, RoundedCornerShape(8.dp))
                    .padding(4.dp)
            ) {
                Button(
                    onClick = { orderSide = "BUY" },
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (orderSide == "BUY") OpenAlgoColors.ProfitGreen else Color.Transparent,
                        contentColor = if (orderSide == "BUY") Color.White else OpenAlgoColors.TextSecondaryDark
                    ),
                    shape = RoundedCornerShape(6.dp)
                ) {
                    Text("BUY / LONG", fontWeight = FontWeight.Bold, fontSize = 13.sp)
                }

                Button(
                    onClick = { orderSide = "SELL" },
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (orderSide == "SELL") OpenAlgoColors.LossCrimson else Color.Transparent,
                        contentColor = if (orderSide == "SELL") Color.White else OpenAlgoColors.TextSecondaryDark
                    ),
                    shape = RoundedCornerShape(6.dp)
                ) {
                    Text("SELL / SHORT", fontWeight = FontWeight.Bold, fontSize = 13.sp)
                }
            }

            // Order Form Inputs
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(8.dp))
                    .background(OpenAlgoColors.SurfaceCard)
                    .border(1.dp, OpenAlgoColors.HairlineBorder, RoundedCornerShape(8.dp))
                    .padding(16.dp)
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(
                        text = "ORDER PARAMETERS",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = OpenAlgoColors.TextSecondaryDark
                    )

                    OutlinedTextField(
                        value = symbol,
                        onValueChange = { symbol = it },
                        label = { Text("Symbol") },
                        modifier = Modifier.fillMaxWidth()
                    )

                    OutlinedTextField(
                        value = quantity,
                        onValueChange = { quantity = it },
                        label = { Text("Quantity") },
                        modifier = Modifier.fillMaxWidth()
                    )

                    OutlinedTextField(
                        value = limitPrice,
                        onValueChange = { limitPrice = it },
                        label = { Text("Limit Price ($)") },
                        modifier = Modifier.fillMaxWidth()
                    )

                    Button(
                        onClick = { /* Execute Order */ },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(48.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = if (orderSide == "BUY") OpenAlgoColors.ProfitGreen else OpenAlgoColors.LossCrimson
                        ),
                        shape = RoundedCornerShape(8.dp)
                    ) {
                        Text(
                            text = "EXECUTE $orderSide ORDER",
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                }
            }
        }
    }
}
