package ru.voitos.app.ui

import android.util.Base64
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.model.ExecutorRole
import ru.voitos.app.ui.theme.VoitosColors

/**
 * Регистрация исполнителя — шаги как в MAX-боте:
 * роль → опыт/техника → [госномер] → телефон → НП → банк → телефон перевода → [документ].
 */
@Composable
fun ExecutorRegisterScreen(
    client: VoitosApiClient,
    onDone: () -> Unit,
    onBack: () -> Unit,
) {
    var roles by remember { mutableStateOf<List<ExecutorRole>>(emptyList()) }
    var step by remember { mutableIntStateOf(0) }
    var role by remember { mutableStateOf<ExecutorRole?>(null) }
    var label by remember { mutableStateOf("") }
    var plate by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var locality by remember { mutableStateOf("") }
    var bank by remember { mutableStateOf("") }
    var payoutPhone by remember { mutableStateOf("") }
    var samePayout by remember { mutableStateOf(true) }
    var qualB64 by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    LaunchedEffect(Unit) {
        try {
            roles = client.executorRoles().items
            val me = runCatching { client.me() }.getOrNull()
            if (me != null) {
                if (phone.isBlank()) phone = me.phone
                if (locality.isBlank()) locality = me.locality
            }
        } catch (e: Exception) {
            error = friendlyNetworkError(e)
        }
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            try {
                val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    ?: throw IllegalStateException("Не удалось прочитать файл")
                qualB64 = Base64.encodeToString(bytes, Base64.NO_WRAP)
                message = "Документ выбран"
            } catch (e: Exception) {
                error = e.message
            }
        }
    }

    fun nextAfterLabel() {
        if (role?.isEquipment == true) step = 2 else step = 3
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        TextButton(onClick = onBack) { Text("← Назад", color = VoitosColors.Accent) }
        Text("Стать исполнителем", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Spacer(modifier = Modifier.height(12.dp))
        error?.let { Text(it, color = VoitosColors.Danger) }
        message?.let { Text(it, color = VoitosColors.Ok) }

        when (step) {
            0 -> {
                Text("Кем работаете? Выберите роль", color = VoitosColors.Text)
                Spacer(modifier = Modifier.height(8.dp))
                roles.forEachIndexed { i, r ->
                    TextButton(
                        onClick = {
                            role = r
                            step = 1
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("${i + 1}. ${r.name}", color = VoitosColors.Text)
                    }
                }
            }
            1 -> {
                val r = role
                Text(
                    if (r?.isEquipment == true) {
                        "Модель или описание техники"
                    } else {
                        "Кратко опишите опыт (или «нет»)"
                    },
                    color = VoitosColors.Text,
                )
                OutlinedTextField(
                    value = label,
                    onValueChange = { label = it },
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    onClick = {
                        if (r?.isEquipment == true && label.trim().length < 2) {
                            error = "Укажите модель/описание техники"
                            return@Button
                        }
                        error = null
                        nextAfterLabel()
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = VoitosColors.Accent),
                ) { Text("Далее") }
            }
            2 -> {
                Text("Госномер (или оставьте пустым)", color = VoitosColors.Text)
                OutlinedTextField(
                    value = plate,
                    onValueChange = { plate = it },
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    onClick = { step = 3 },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = VoitosColors.Accent),
                ) { Text("Далее") }
            }
            3 -> {
                Text("Телефон для связи", color = VoitosColors.Text)
                OutlinedTextField(
                    value = phone,
                    onValueChange = { phone = it },
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    onClick = {
                        if (phone.filter { it.isDigit() }.length < 10) {
                            error = "Нужен корректный телефон"
                            return@Button
                        }
                        error = null
                        step = 4
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = VoitosColors.Accent),
                ) { Text("Далее") }
            }
            4 -> {
                Text("Населённый пункт, где работаете", color = VoitosColors.Text)
                OutlinedTextField(
                    value = locality,
                    onValueChange = { locality = it },
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    onClick = {
                        if (locality.trim().length < 2) {
                            error = "Укажите населённый пункт"
                            return@Button
                        }
                        error = null
                        step = 5
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = VoitosColors.Accent),
                ) { Text("Далее") }
            }
            5 -> {
                Text("Банк для перевода (Сбер, Тинькофф…)", color = VoitosColors.Text)
                OutlinedTextField(
                    value = bank,
                    onValueChange = { bank = it },
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    onClick = {
                        if (bank.trim().length < 2) {
                            error = "Укажите банк"
                            return@Button
                        }
                        error = null
                        step = 6
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = VoitosColors.Accent),
                ) { Text("Далее") }
            }
            6 -> {
                Text("Телефон для перевода денег", color = VoitosColors.Text)
                TextButton(onClick = { samePayout = true; payoutPhone = phone }) {
                    Text("Тот же, что для связи", color = VoitosColors.Accent2)
                }
                if (!samePayout || payoutPhone != phone) {
                    OutlinedTextField(
                        value = payoutPhone,
                        onValueChange = {
                            payoutPhone = it
                            samePayout = false
                        },
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                Button(
                    onClick = {
                        val pay = if (samePayout || payoutPhone.isBlank()) phone else payoutPhone
                        payoutPhone = pay
                        error = null
                        step = if (role?.requiresQualificationDocs == true) 7 else 8
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = VoitosColors.Accent),
                ) { Text("Далее") }
            }
            7 -> {
                Text("Фото документа о квалификации", color = VoitosColors.Text)
                Button(
                    onClick = { picker.launch("image/*") },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Выбрать фото") }
                if (qualB64.isNotBlank()) {
                    Text("Файл выбран ✓", color = VoitosColors.Ok)
                }
                Button(
                    onClick = {
                        if (qualB64.isBlank()) {
                            error = "Нужен документ"
                            return@Button
                        }
                        error = null
                        step = 8
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = VoitosColors.Accent),
                ) { Text("Далее") }
            }
            8 -> {
                Text("Отправить анкету на проверку?", color = VoitosColors.Text)
                if (loading) CircularProgressIndicator(modifier = Modifier.align(Alignment.CenterHorizontally))
                Button(
                    onClick = {
                        val r = role ?: return@Button
                        scope.launch {
                            loading = true
                            error = null
                            try {
                                val res = client.registerExecutor(
                                    roleId = r.id,
                                    equipmentLabel = label.ifBlank { "нет" },
                                    plateNumber = plate,
                                    phone = phone,
                                    locality = locality,
                                    bankName = bank,
                                    payoutPhone = payoutPhone.ifBlank { phone },
                                    qualBase64 = qualB64,
                                )
                                message = res.message.ifBlank { "Анкета отправлена" }
                                onDone()
                            } catch (e: Exception) {
                                error = friendlyNetworkError(e)
                            } finally {
                                loading = false
                            }
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !loading,
                    colors = ButtonDefaults.buttonColors(containerColor = VoitosColors.Accent),
                ) { Text("Отправить") }
            }
        }
    }
}
