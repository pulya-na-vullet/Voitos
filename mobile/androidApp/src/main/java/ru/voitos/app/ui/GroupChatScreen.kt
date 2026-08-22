package ru.voitos.app.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.model.GroupChatMessage
import ru.voitos.app.model.ServiceGroupBrief
import ru.voitos.app.ui.theme.VoitosColors
import ru.voitos.app.ui.theme.voitosPrimaryButtonColors

@Composable
fun GroupChatScreen(
    client: VoitosApiClient,
    onBack: () -> Unit,
    initialGroupId: Int? = null,
) {
    var groups by remember { mutableStateOf<List<ServiceGroupBrief>>(emptyList()) }
    var selectedGroupId by remember { mutableStateOf(initialGroupId) }
    var messages by remember { mutableStateOf<List<GroupChatMessage>>(emptyList()) }
    var authorPaid by remember { mutableStateOf<Map<String, List<Boolean>>>(emptyMap()) }
    var draft by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var sending by remember { mutableStateOf(false) }
    var menuOpen by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    val selected = groups.firstOrNull { it.id == selectedGroupId } ?: groups.firstOrNull()

    BackHandler(enabled = true) {
        if (menuOpen) {
            menuOpen = false
        } else {
            onBack()
        }
    }

    fun loadGroups() {
        scope.launch {
            try {
                groups = client.groups().items
                if (selectedGroupId == null || groups.none { it.id == selectedGroupId }) {
                    selectedGroupId = groups.firstOrNull()?.id
                }
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
            }
        }
    }

    fun loadMessages(silent: Boolean = false) {
        val gid = selectedGroupId ?: return
        scope.launch {
            if (!silent) loading = true
            try {
                val res = client.groupMessages(gid)
                messages = res.items
                authorPaid = res.authorPaid
                error = null
            } catch (e: Exception) {
                if (!silent) error = friendlyNetworkError(e)
            } finally {
                if (!silent) loading = false
            }
        }
    }

    LaunchedEffect(Unit) { loadGroups() }

    LaunchedEffect(selectedGroupId) {
        if (selectedGroupId != null) {
            messages = emptyList()
            authorPaid = emptyMap()
            loadMessages(silent = false)
        } else {
            loading = false
        }
    }

    LaunchedEffect(selectedGroupId) {
        val gid = selectedGroupId ?: return@LaunchedEffect
        while (isActive) {
            delay(5_000)
            if (selectedGroupId != gid) break
            try {
                val lastId = messages.lastOrNull()?.id
                val res = if (lastId != null) {
                    client.groupMessages(gid, afterId = lastId)
                } else {
                    client.groupMessages(gid)
                }
                authorPaid = res.authorPaid
                if (lastId == null) {
                    if (res.items.isNotEmpty()) messages = res.items
                } else if (res.items.isNotEmpty()) {
                    val known = messages.map { it.id }.toHashSet()
                    val fresh = res.items.filter { it.id !in known }
                    if (fresh.isNotEmpty()) messages = messages + fresh
                }
            } catch (_: Exception) {
            }
        }
    }

    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) {
            listState.animateScrollToItem(messages.lastIndex)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                VoitosBackButton(onClick = onBack)
                Text("Чаты", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
            }
        }
        Spacer(modifier = Modifier.height(8.dp))

        when {
            groups.isEmpty() && !loading -> {
                Text(
                    "Вы пока не состоите ни в одной группе — чат появится после добавления администратором.",
                    color = VoitosColors.Muted,
                )
            }
            groups.size > 1 -> {
                Box {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(12.dp))
                            .border(1.dp, VoitosColors.Line, RoundedCornerShape(12.dp))
                            .background(VoitosColors.BgSoft)
                            .clickable { menuOpen = true }
                            .padding(horizontal = 14.dp, vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text("Группа", color = VoitosColors.Muted, style = MaterialTheme.typography.labelSmall)
                            Text(
                                selected?.name?.ifBlank { "Группа" } ?: "Выберите группу",
                                color = VoitosColors.Text,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
                        Text("▾", color = VoitosColors.Accent2)
                    }
                    DropdownMenu(
                        expanded = menuOpen,
                        onDismissRequest = { menuOpen = false },
                    ) {
                        groups.forEach { g ->
                            DropdownMenuItem(
                                text = {
                                    Text(
                                        buildString {
                                            append(g.name.ifBlank { "Группа ${g.id}" })
                                            if (g.memberCount > 0) append(" · ${g.memberCount}")
                                        },
                                    )
                                },
                                onClick = {
                                    selectedGroupId = g.id
                                    menuOpen = false
                                },
                            )
                        }
                    }
                }
                selected?.let { g ->
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(
                        "Свободный баланс группы: ${formatRub(g.freeBalance)} ₽",
                        color = VoitosColors.Accent2,
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                Spacer(modifier = Modifier.height(10.dp))
            }
            selected != null -> {
                Text(
                    selected.name.ifBlank { "Группа" },
                    color = VoitosColors.Text,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    "Свободный баланс группы: ${formatRub(selected.freeBalance)} ₽",
                    color = VoitosColors.Accent2,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(modifier = Modifier.height(8.dp))
            }
        }

        error?.let { NetworkErrorText(it) }

        if (loading && messages.isEmpty() && selectedGroupId != null) {
            VoitosListSkeleton(rows = 4)
        }

        if (selectedGroupId != null) {
            LazyColumn(
                state = listState,
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = PaddingValues(vertical = 8.dp),
            ) {
                if (!loading && messages.isEmpty()) {
                    item {
                        Text("Пока нет сообщений — напишите первым.", color = VoitosColors.Muted)
                    }
                }
                items(messages, key = { it.id }) { msg ->
                    val dots = msg.authorId?.let { authorPaid[it.toString()] }.orEmpty()
                    ChatBubble(msg, paymentDots = dots)
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.Bottom,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = draft,
                    onValueChange = { if (it.length <= 2000) draft = it },
                    modifier = Modifier
                        .weight(1f)
                        .heightIn(min = 52.dp),
                    placeholder = { Text("Сообщение…") },
                    maxLines = 4,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = VoitosColors.Text,
                        unfocusedTextColor = VoitosColors.Text,
                        focusedBorderColor = VoitosColors.Accent2,
                        unfocusedBorderColor = VoitosColors.Line,
                        cursorColor = VoitosColors.Accent2,
                    ),
                )
                Button(
                    onClick = {
                        val text = draft.trim()
                        val gid = selectedGroupId ?: return@Button
                        if (text.isEmpty() || sending) return@Button
                        scope.launch {
                            sending = true
                            error = null
                            try {
                                val res = client.sendGroupMessage(gid, text)
                                val sent = res.message
                                if (sent != null && messages.none { it.id == sent.id }) {
                                    messages = messages + sent
                                }
                                draft = ""
                            } catch (e: Exception) {
                                error = friendlyNetworkError(e)
                            } finally {
                                sending = false
                            }
                        }
                    },
                    enabled = !sending && draft.trim().isNotEmpty(),
                    colors = voitosPrimaryButtonColors(),
                ) {
                    Text(if (sending) "…" else "➤")
                }
            }
        } else if (!loading) {
            Spacer(modifier = Modifier.weight(1f))
        }
    }
}

@Composable
private fun ChatBubble(msg: GroupChatMessage, paymentDots: List<Boolean> = emptyList()) {
    val mine = msg.isMine
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start,
    ) {
        if (!mine) {
            AvatarDot(url = msg.authorAvatarUrl, name = msg.authorName)
            Spacer(modifier = Modifier.size(8.dp))
        }
        Column(
            modifier = Modifier
                .widthIn(max = 300.dp)
                .clip(RoundedCornerShape(14.dp))
                .background(
                    if (mine) VoitosColors.Accent2.copy(alpha = 0.22f)
                    else VoitosColors.Panel.copy(alpha = 0.9f),
                )
                .padding(horizontal = 12.dp, vertical = 8.dp),
            horizontalAlignment = if (mine) Alignment.End else Alignment.Start,
        ) {
            if (!mine && msg.authorName.isNotBlank()) {
                Text(
                    msg.authorName,
                    color = VoitosColors.Accent2,
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(modifier = Modifier.height(2.dp))
            }
            Text(msg.text, color = VoitosColors.Text, style = MaterialTheme.typography.bodyMedium)
            Spacer(modifier = Modifier.height(4.dp))
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    formatChatTime(msg.createdAt),
                    color = VoitosColors.Muted,
                    style = MaterialTheme.typography.labelSmall,
                )
                if (paymentDots.isNotEmpty()) {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(2.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        paymentDots.forEach { paid ->
                            Box(
                                modifier = Modifier
                                    .size(5.dp)
                                    .clip(CircleShape)
                                    .background(if (paid) VoitosColors.Ok else VoitosColors.Danger),
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun AvatarDot(url: String, name: String) {
    val letter = name.trim().firstOrNull()?.uppercaseChar()?.toString() ?: "?"
    Box(
        modifier = Modifier
            .size(32.dp)
            .clip(CircleShape)
            .background(VoitosColors.BgSoft)
            .border(1.dp, VoitosColors.Line, CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        if (url.isNotBlank()) {
            AsyncImage(
                model = ImageRequest.Builder(LocalContext.current).data(url).crossfade(true).build(),
                contentDescription = name,
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop,
            )
        } else {
            Text(letter, color = VoitosColors.Muted, style = MaterialTheme.typography.labelMedium)
        }
    }
}

private fun formatChatTime(raw: String): String {
    if (raw.isBlank()) return ""
    // ISO → MM.DD HH:MM (месяц.день часы:минуты)
    val normalized = raw.replace('T', ' ')
    val datePart = normalized.take(10)
    val timePart = normalized.drop(11).take(5)
    if (datePart.length == 10 && datePart[4] == '-' && datePart[7] == '-') {
        val mm = datePart.substring(5, 7)
        val dd = datePart.substring(8, 10)
        return if (timePart.length == 5) "$mm.$dd $timePart" else "$mm.$dd"
    }
    return normalized.take(16)
}

private fun formatRub(value: Double): String {
    val n = value.toLong()
    return "%,d".format(n).replace(',', ' ')
}
