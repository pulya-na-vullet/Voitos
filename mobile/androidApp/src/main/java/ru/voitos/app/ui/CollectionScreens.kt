package ru.voitos.app.ui

import android.util.Base64
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import coil.compose.AsyncImage
import coil.request.ImageRequest
import kotlinx.coroutines.launch
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.model.CollectionBrief
import ru.voitos.app.model.CollectionDetail
import ru.voitos.app.ui.theme.VoitosColors
import ru.voitos.app.ui.theme.voitosPrimaryButtonColors

@Composable
fun CollectionsScreen(
    client: VoitosApiClient,
    onOpen: (Int) -> Unit = {},
    onBack: (() -> Unit)? = null,
    refreshKey: Int = 0,
) {
    var items by remember { mutableStateOf<List<CollectionBrief>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    val scope = rememberCoroutineScope()
    val lifecycleOwner = LocalLifecycleOwner.current

    fun reload() {
        scope.launch {
            loading = true
            error = null
            try {
                items = client.collections().items
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(refreshKey) { reload() }

    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) reload()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                if (onBack != null) {
                    VoitosBackButton(onClick = onBack)
                }
                Text("Сборы", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
            }
            TextButton(onClick = { reload() }) {
                Text("Обновить", color = VoitosColors.Accent2)
            }
        }
        error?.let { Text(it, color = VoitosColors.Danger) }
        if (loading && items.isEmpty()) {
            CircularProgressIndicator(
                modifier = Modifier.align(Alignment.CenterHorizontally).padding(top = 24.dp),
                color = VoitosColors.Accent,
            )
        }
        if (!loading && items.isEmpty() && error == null) {
            Text(
                "Нет активных сборов по вашим группе/группам",
                color = VoitosColors.Muted,
                modifier = Modifier.padding(top = 12.dp),
            )
        }
        LazyColumn(
            verticalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.fillMaxSize().padding(top = 8.dp),
        ) {
            items(items, key = { it.id }) { c ->
                CollectionListCard(item = c, onClick = { onOpen(c.id) })
            }
        }
    }
}

@Composable
private fun CollectionListCard(item: CollectionBrief, onClick: () -> Unit) {
    val status = collectionPaymentStatus(
        inviteStatus = item.status,
        pendingReceipts = item.pendingReceipts,
        rejectedReceipts = item.rejectedReceipts,
    )
    val photos = item.photoUrls.ifEmpty {
        listOfNotNull(item.coverPhotoUrl.takeIf { it.isNotBlank() })
    }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(VoitosColors.Panel.copy(alpha = 0.86f))
            .clickable(onClick = onClick),
    ) {
        if (photos.isNotEmpty()) {
            CollectionPhotoCarousel(
                photos = photos,
                contentDescription = item.title,
                height = 140.dp,
            )
        }
        Column(modifier = Modifier.padding(14.dp)) {
            Text(item.title, style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
            Spacer(modifier = Modifier.height(4.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                val meta = listOfNotNull(
                    item.categoryLabel.ifBlank { item.category }.takeIf { it.isNotBlank() },
                    "${item.amountDue.toInt()} ₽",
                ).joinToString(" · ")
                if (meta.isNotBlank()) {
                    Text(meta, color = VoitosColors.Muted)
                    Text("·", color = VoitosColors.Muted)
                }
                CollectionStatusText(status)
            }
            if (item.description.isNotBlank()) {
                Spacer(modifier = Modifier.height(6.dp))
                Text(
                    item.description,
                    color = VoitosColors.Text.copy(alpha = 0.85f),
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 2,
                )
            }
        }
    }
}

@Composable
fun CollectionDetailScreen(
    client: VoitosApiClient,
    collectionId: Int,
    onBack: () -> Unit,
) {
    var detail by remember { mutableStateOf<CollectionDetail?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var uploading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    fun reload() {
        scope.launch {
            loading = true
            error = null
            try {
                detail = client.collection(collectionId)
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(collectionId) { reload() }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            uploading = true
            error = null
            try {
                val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    ?: throw IllegalStateException("Не удалось прочитать файл")
                val b64 = Base64.encodeToString(bytes, Base64.NO_WRAP)
                val mime = context.contentResolver.getType(uri).orEmpty()
                val ext = when {
                    mime.contains("png") -> "png"
                    mime.contains("webp") -> "webp"
                    mime.contains("pdf") -> "pdf"
                    else -> "jpg"
                }
                val res = client.uploadCollectionReceipt(
                    collectionId,
                    b64,
                    "collection_${System.currentTimeMillis()}.$ext",
                )
                message = res.message.ifBlank { "Чек отправлен на проверку" }
                reload()
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
            } finally {
                uploading = false
            }
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        VoitosBackButton(onClick = onBack)
        Text("Сбор", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Spacer(modifier = Modifier.height(8.dp))
        error?.let { Text(it, color = VoitosColors.Danger) }
        message?.let { Text(it, color = VoitosColors.Ok) }
        if (loading && detail == null) {
            CircularProgressIndicator(
                modifier = Modifier.align(Alignment.CenterHorizontally),
                color = VoitosColors.Accent,
            )
        }
        detail?.let { c ->
            val photos = c.photoUrls.ifEmpty {
                listOfNotNull(c.coverPhotoUrl.takeIf { it.isNotBlank() })
            }
            if (photos.isNotEmpty()) {
                CollectionPhotoCarousel(
                    photos = photos,
                    contentDescription = c.title,
                    height = 220.dp,
                )
                Spacer(modifier = Modifier.height(12.dp))
            } else {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(120.dp)
                        .clip(RoundedCornerShape(14.dp))
                        .background(VoitosColors.BgSoft),
                    contentAlignment = Alignment.Center,
                ) {
                    Text("Фото пока нет", color = VoitosColors.Muted)
                }
                Spacer(modifier = Modifier.height(10.dp))
            }

            PanelCard {
                Text(c.title, style = MaterialTheme.typography.titleLarge, color = VoitosColors.Text)
                Spacer(modifier = Modifier.height(4.dp))
                Row(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    val cat = c.categoryLabel.ifBlank { c.category }
                    if (cat.isNotBlank()) {
                        Text(cat, color = VoitosColors.Muted)
                        Text("·", color = VoitosColors.Muted)
                    }
                    CollectionStatusText(
                        collectionPaymentStatus(
                            inviteStatus = c.status,
                            pendingReceipts = c.pendingReceipts,
                            rejectedReceipts = c.rejectedReceipts,
                        ),
                    )
                }
                Spacer(modifier = Modifier.height(10.dp))
                Text(
                    "К оплате: ${c.amountDue.toInt()} ₽",
                    style = MaterialTheme.typography.titleMedium,
                    color = VoitosColors.Accent2,
                )
                if (c.amountPaid > 0) {
                    Text("Оплачено: ${c.amountPaid.toInt()} ₽", color = VoitosColors.Ok)
                }
                c.eventAt?.let {
                    Spacer(modifier = Modifier.height(4.dp))
                    Text("Мероприятие: ${formatEventAt(it)}", color = VoitosColors.Muted)
                }
                Text(
                    "Оплатили: ${c.paidCount} из ${c.inviteCount}",
                    color = VoitosColors.Muted,
                )
                if (c.description.isNotBlank()) {
                    Spacer(modifier = Modifier.height(10.dp))
                    HorizontalDivider(color = VoitosColors.Line)
                    Spacer(modifier = Modifier.height(10.dp))
                    Text(c.description, color = VoitosColors.Text)
                }
            }

            Spacer(modifier = Modifier.height(12.dp))

            PanelCard {
                Text("Реквизиты для перевода", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Accent2)
                Spacer(modifier = Modifier.height(8.dp))
                RequisiteLine("Получатель", c.paymentName.ifBlank { "—" })
                RequisiteLine("Телефон", c.paymentPhone.ifBlank { "—" })
                RequisiteLine("Банк", c.paymentBank.ifBlank { "—" })
                if (c.paymentStatus.isNotBlank()) {
                    RequisiteLine("Статус", c.paymentStatus)
                }
                Spacer(modifier = Modifier.height(6.dp))
                Text(
                    "Переведите сумму сбора и приложите фото чека ниже.",
                    color = VoitosColors.Muted,
                    style = MaterialTheme.typography.bodySmall,
                )
            }

            Spacer(modifier = Modifier.height(16.dp))

            when {
                c.status == "paid" -> {
                    Text("Сбор оплачен ✓", color = VoitosColors.Ok, style = MaterialTheme.typography.titleMedium)
                }
                c.pendingReceipts > 0 -> {
                    Text(
                        "Чек на проверке (${c.pendingReceipts}). Можно приложить ещё, если нужно.",
                        color = VoitosColors.Gold,
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Button(
                        onClick = { picker.launch("image/*") },
                        modifier = Modifier.fillMaxWidth(),
                        enabled = !uploading,
                        colors = voitosPrimaryButtonColors(),
                    ) {
                        Text(if (uploading) "Отправка…" else "Приложить ещё чек")
                    }
                }
                c.rejectedReceipts > 0 && c.canPay -> {
                    Text(
                        "Чек отклонён — пришлите корректный ещё раз.",
                        color = VoitosColors.Burgundy,
                        style = MaterialTheme.typography.titleMedium,
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Button(
                        onClick = { picker.launch("image/*") },
                        modifier = Modifier.fillMaxWidth(),
                        enabled = !uploading,
                        colors = voitosPrimaryButtonColors(),
                    ) {
                        Text(if (uploading) "Отправка…" else "Приложить чек оплаты")
                    }
                }
                c.canPay -> {
                    Button(
                        onClick = { picker.launch("image/*") },
                        modifier = Modifier.fillMaxWidth(),
                        enabled = !uploading,
                        colors = voitosPrimaryButtonColors(),
                    ) {
                        Text(if (uploading) "Отправка…" else "Приложить чек оплаты")
                    }
                }
                else -> {
                    Text("Оплата сейчас недоступна", color = VoitosColors.Muted)
                }
            }
        }
    }
}

@Composable
private fun CollectionPhotoCarousel(
    photos: List<String>,
    contentDescription: String,
    height: Dp,
) {
    val context = LocalContext.current
    val pagerState = rememberPagerState(pageCount = { photos.size })
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(height)
            .clip(RoundedCornerShape(14.dp))
            .background(VoitosColors.BgSoft),
    ) {
        HorizontalPager(
            state = pagerState,
            modifier = Modifier.fillMaxSize(),
        ) { idx ->
            AsyncImage(
                model = ImageRequest.Builder(context)
                    .data(photos[idx])
                    .crossfade(true)
                    .build(),
                contentDescription = "$contentDescription · фото ${idx + 1}",
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop,
            )
        }
        if (photos.size > 1) {
            Text(
                "${pagerState.currentPage + 1} / ${photos.size}",
                color = VoitosColors.Text,
                style = MaterialTheme.typography.labelMedium,
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .padding(10.dp)
                    .background(VoitosColors.Bg.copy(alpha = 0.55f), RoundedCornerShape(8.dp))
                    .padding(horizontal = 8.dp, vertical = 4.dp),
            )
            Row(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = 10.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                repeat(photos.size) { i ->
                    Box(
                        modifier = Modifier
                            .height(6.dp)
                            .then(
                                if (i == pagerState.currentPage) Modifier.width(16.dp)
                                else Modifier.width(6.dp),
                            )
                            .clip(RoundedCornerShape(999.dp))
                            .background(
                                if (i == pagerState.currentPage) VoitosColors.Accent2
                                else VoitosColors.Text.copy(alpha = 0.35f),
                            ),
                    )
                }
            }
        }
    }
}

@Composable
private fun RequisiteLine(label: String, value: String) {
    Column(modifier = Modifier.padding(vertical = 4.dp)) {
        Text(label, style = MaterialTheme.typography.labelSmall, color = VoitosColors.Muted)
        Text(value, style = MaterialTheme.typography.bodyLarge, color = VoitosColors.Text)
    }
}

private data class CollectionPaymentStatus(
    val label: String,
    val color: Color,
    val glow: Boolean = false,
)

private fun collectionPaymentStatus(
    inviteStatus: String,
    pendingReceipts: Int,
    rejectedReceipts: Int,
): CollectionPaymentStatus = when {
    inviteStatus == "paid" -> CollectionPaymentStatus("Оплачено", VoitosColors.Ok)
    inviteStatus == "declined" -> CollectionPaymentStatus("Отказ", VoitosColors.Muted)
    inviteStatus == "cancelled" -> CollectionPaymentStatus("Отменено", VoitosColors.Muted)
    pendingReceipts > 0 -> CollectionPaymentStatus("На проверке", VoitosColors.Gold, glow = true)
    rejectedReceipts > 0 -> CollectionPaymentStatus("Чек отклонён", VoitosColors.Burgundy)
    else -> CollectionPaymentStatus("Ожидает оплаты", VoitosColors.Gold, glow = true)
}

@Composable
private fun CollectionStatusText(status: CollectionPaymentStatus) {
    Text(
        text = status.label,
        color = status.color,
        style = MaterialTheme.typography.bodyMedium,
        fontWeight = FontWeight.SemiBold,
        modifier = if (status.glow) {
            Modifier.shadow(
                elevation = 6.dp,
                shape = RoundedCornerShape(4.dp),
                ambientColor = VoitosColors.GoldGlow,
                spotColor = VoitosColors.GoldGlow,
            )
        } else {
            Modifier
        },
    )
}

private fun formatEventAt(raw: String): String {
    val date = raw.take(16).replace('T', ' ')
    return if (date.length >= 10 && date[4] == '-') {
        val d = date.take(10).split("-")
        val time = date.drop(11).take(5)
        "${d[2]}.${d[1]}.${d[0]}" + if (time.isNotBlank()) " $time" else ""
    } else {
        raw
    }
}
