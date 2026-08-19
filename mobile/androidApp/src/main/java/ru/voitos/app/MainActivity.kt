package ru.voitos.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier.Modifier
import androidx.compose.ui.unit.dp

/** Skeleton host. Wire VoitosApiClient + DeepLinks next. */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Column(modifier = Modifier.padding(24.dp)) {
                    Text("Voitos KMP", style = MaterialTheme.typography.headlineMedium)
                    Text("API: ${VoitosApi.DEFAULT_BASE_URL}")
                    Text("См. docs/mobile/ — стори и пуши")
                }
            }
        }
    }
}
