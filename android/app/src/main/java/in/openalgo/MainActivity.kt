package `in`.openalgo

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import `in`.openalgo.ui.screens.DashboardScreen
import `in`.openalgo.ui.screens.TradingScreen
import `in`.openalgo.ui.theme.OpenAlgoColors
import `in`.openalgo.ui.theme.OpenAlgoTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            OpenAlgoTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = OpenAlgoColors.ObsidianDark
                ) {
                    var currentScreen by remember { mutableStateOf("dashboard") }

                    when (currentScreen) {
                        "dashboard" -> DashboardScreen(
                            onNavigateToTrading = { currentScreen = "trading" }
                        )
                        "trading" -> TradingScreen(
                            onNavigateBack = { currentScreen = "dashboard" }
                        )
                    }
                }
            }
        }
    }
}
