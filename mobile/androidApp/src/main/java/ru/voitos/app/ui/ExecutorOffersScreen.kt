package ru.voitos.app.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.snapping.rememberSnapFlingBehavior
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
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.model.ExecutorJobBrief
import ru.voitos.app.model.ExecutorOfferBrief
import ru.voitos.app.model.ExecutorProfileBrief
import ru.voitos.app.model.ExecutorScheduleEvent
import ru.voitos.app.ui.theme.VoitosColors
import ru.voitos.app.ui.theme.voitosPrimaryButtonColors
import ru.voitos.app.ui.theme.voitosSecondaryButtonColors

private val DAY_NAMES_RU = listOf("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
private val MONTH_FMT = DateTimeFormatter.ofPattern("d MMM", Locale("ru"))
private val RANGE_FMT = DateTimeFormatter.ofPattern("d MMM", Locale("ru"))

@Composable
fun ExecutorOffersScreen(
    client: VoitosApiClient,
    onBack: (() -> Unit)? = null,
    onOpenWorkRequest: (Int) -> Unit = {},
) {
    var profiles by remember { mutableStateOf<List<ExecutorProfileBrief>>(emptyList()) }
    var offers by remember { mutableStateOf<List<ExecutorOfferBrief>>(emptyList()) }
    var jobs by remember { mutableStateOf<List<ExecutorJobBrief>>(emptyList()) }
    var schedule by remember { mutableStateOf<List<ExecutorScheduleEvent>>(emptyList()) }
    var weekStart by remember { mutableStateOf(mondayOf(LocalDate.now())) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var busyId by remember { mutableStateOf<Int?>(null) }
    var slotDrafts by remember { mutableStateOf<Map<Int, List<String>>>(emptyMap()) }
    var proposeForJobId by remember { mutableStateOf<Int?>(null) }
    val scope = rememberCoroutineScope()

    fun offersFor(profile: ExecutorProfileBrief): List<ExecutorOfferBrief> =
        offers.filter { offer ->
            when {
                profile.roleId > 0 && offer.roleId > 0 -> offer.roleId == profile.roleId
                else -> offer.roleName.equals(profile.roleName, ignoreCase = true)
            }
        }

    fun reload(keepMessage: Boolean = false) {
        scope.launch {
            loading = true
            error = null
            if (!keepMessage) message = null
            try {
                profiles = client.executorMe().profiles
                offers = client.executorOffers().items
                jobs = runCatching { client.executorJobs().items }.getOrDefault(emptyList())
                schedule = runCatching {
                    client.executorSchedule(weekStart.toString()).items
                }.getOrDefault(emptyList())
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) { reload() }
    LaunchedEffect(weekStart) {
        schedule = runCatching {
            client.executorSchedule(weekStart.toString()).items
        }.getOrDefault(emptyList())
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        if (onBack != null) {
            VoitosBackButton(onClick = onBack)
        }
        Text("Работа", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Spacer(modifier = Modifier.height(8.dp))
        error?.let { NetworkErrorText(it) }
        message?.let { Text(it, color = VoitosColors.Ok) }
        if (loading) {
            VoitosListSkeleton(rows = 3)
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                item {
                    WeekScheduleFrame(
                        weekStart = weekStart,
                        events = schedule,
                        onPrevWeek = { weekStart = weekStart.minusWeeks(1) },
                        onNextWeek = { weekStart = weekStart.plusWeeks(1) },
                        onOpenEvent = onOpenWorkRequest,
                    )
                }

                if (jobs.isNotEmpty()) {
                    item {
                        Text(
                            "Согласование времени",
                            style = MaterialTheme.typography.titleMedium,
                            color = VoitosColors.Accent2,
                        )
                    }
                    items(jobs, key = { "job-${it.id}" }) { job ->
                        val drafts = slotDrafts[job.id].orEmpty()
                        PanelCard {
                            Text(
                                "#${job.id} · ${job.roleName}",
                                style = MaterialTheme.typography.titleMedium,
                                color = VoitosColors.Text,
                            )
                            Text(job.clientName, color = VoitosColors.Muted)
                            if (job.clientPhone.isNotBlank()) {
                                Text("Тел. клиента: ${job.clientPhone}", color = VoitosColors.Text)
                            }
                            if (job.acceptsAtHome) {
                                Text(
                                    "Приём на дому: ${job.masterAddress.ifBlank { "адрес уточните" }}",
                                    color = VoitosColors.Muted,
                                )
                            } else {
                                if (job.clientLocality.isNotBlank() || job.clientAddress.isNotBlank()) {
                                    Text(
                                        listOf(job.clientLocality, job.clientAddress)
                                            .filter { it.isNotBlank() }
                                            .joinToString(", "),
                                        color = VoitosColors.Muted,
                                    )
                                }
                            }
                            Text(job.description, color = VoitosColors.Text)
                            Spacer(modifier = Modifier.height(8.dp))
                            if (job.proposedSlots.isNotEmpty()) {
                                Text(
                                    "Окна отправлены клиенту:\n" +
                                        job.proposedSlots.mapIndexed { i, s -> "${i + 1}. $s" }
                                            .joinToString("\n"),
                                    color = VoitosColors.Ok,
                                )
                            } else {
                                Text(
                                    "Добавьте окна: дата в календаре, время — прокруткой.",
                                    color = VoitosColors.Muted,
                                    style = MaterialTheme.typography.bodySmall,
                                )
                                if (drafts.isNotEmpty()) {
                                    Spacer(modifier = Modifier.height(6.dp))
                                    drafts.forEachIndexed { idx, slot ->
                                        Row(
                                            modifier = Modifier.fillMaxWidth(),
                                            horizontalArrangement = Arrangement.SpaceBetween,
                                            verticalAlignment = Alignment.CenterVertically,
                                        ) {
                                            Text(
                                                "${idx + 1}. $slot",
                                                color = VoitosColors.Text,
                                                modifier = Modifier.weight(1f),
                                            )
                                            TextButton(
                                                onClick = {
                                                    slotDrafts = slotDrafts + (
                                                        job.id to drafts.filterIndexed { i, _ -> i != idx }
                                                        )
                                                },
                                            ) { Text("Убрать", color = VoitosColors.Danger) }
                                        }
                                    }
                                }
                                Spacer(modifier = Modifier.height(8.dp))
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Button(
                                        onClick = { proposeForJobId = job.id },
                                        colors = voitosSecondaryButtonColors(),
                                    ) { Text("Добавить окно") }
                                    Button(
                                        onClick = {
                                            scope.launch {
                                                busyId = job.id
                                                error = null
                                                try {
                                                    val res = client.proposeWorkRequestSlots(job.id, drafts)
                                                    message = res.message.ifBlank { "Окна отправлены клиенту" }
                                                    slotDrafts = slotDrafts - job.id
                                                    reload(keepMessage = true)
                                                } catch (e: Exception) {
                                                    error = friendlyNetworkError(e)
                                                } finally {
                                                    busyId = null
                                                }
                                            }
                                        },
                                        enabled = busyId != job.id && drafts.isNotEmpty(),
                                        colors = voitosPrimaryButtonColors(),
                                    ) { Text("Отправить клиенту") }
                                }
                            }
                        }
                    }
                }

                if (profiles.isEmpty()) {
                    item {
                        Text("Вы ещё не зарегистрированы как исполнитель", color = VoitosColors.Muted)
                    }
                }
                items(profiles, key = { it.id }) { profile ->
                    val roleOffers = offersFor(profile)
                    PanelCard {
                        Text(profile.roleName, style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
                        Spacer(modifier = Modifier.height(6.dp))
                        if (roleOffers.isEmpty()) {
                            Text(
                                "Нет новых предложений для этой роли",
                                color = VoitosColors.Muted,
                            )
                        } else {
                            roleOffers.forEach { offer ->
                                Column(modifier = Modifier.padding(vertical = 6.dp)) {
                                    Text(
                                        "#${offer.workRequestId}",
                                        style = MaterialTheme.typography.titleSmall,
                                        color = VoitosColors.Text,
                                    )
                                    if (offer.clientName.isNotBlank()) {
                                        Text(offer.clientName, color = VoitosColors.Muted)
                                    }
                                    Text(offer.locality.ifBlank { "НП не указан" }, color = VoitosColors.Muted)
                                    if (offer.address.isNotBlank()) {
                                        Text(offer.address, color = VoitosColors.Muted)
                                    }
                                    if (offer.clientPhone.isNotBlank()) {
                                        Text("Тел.: ${offer.clientPhone}", color = VoitosColors.Text)
                                    }
                                    if (offer.acceptsAtHome) {
                                        Text("Роль: приём на дому", color = VoitosColors.Accent2)
                                    }
                                    Text(offer.description, color = VoitosColors.Text)
                                    Spacer(modifier = Modifier.height(8.dp))
                                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                        Button(
                                            onClick = {
                                                scope.launch {
                                                    busyId = offer.offerId
                                                    error = null
                                                    try {
                                                        val res = client.respondExecutorOffer(
                                                            offer.offerId,
                                                            accept = true,
                                                        )
                                                        message = res.message
                                                        reload(keepMessage = true)
                                                    } catch (e: Exception) {
                                                        error = friendlyNetworkError(e)
                                                    } finally {
                                                        busyId = null
                                                    }
                                                }
                                            },
                                            enabled = busyId != offer.offerId,
                                            colors = voitosPrimaryButtonColors(),
                                        ) { Text("Беру") }
                                        Button(
                                            onClick = {
                                                scope.launch {
                                                    busyId = offer.offerId
                                                    error = null
                                                    try {
                                                        val res = client.respondExecutorOffer(
                                                            offer.offerId,
                                                            accept = false,
                                                        )
                                                        message = res.message
                                                        reload(keepMessage = true)
                                                    } catch (e: Exception) {
                                                        error = friendlyNetworkError(e)
                                                    } finally {
                                                        busyId = null
                                                    }
                                                }
                                            },
                                            enabled = busyId != offer.offerId,
                                            colors = voitosSecondaryButtonColors(),
                                        ) { Text("Отказ", color = VoitosColors.Danger) }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    proposeForJobId?.let { jobId ->
        ProposeSlotPickerDialog(
            onDismiss = { proposeForJobId = null },
            onAdd = { label ->
                val cur = slotDrafts[jobId].orEmpty()
                if (label !in cur) {
                    slotDrafts = slotDrafts + (jobId to (cur + label))
                }
                proposeForJobId = null
            },
        )
    }
}

@Composable
private fun WeekScheduleFrame(
    weekStart: LocalDate,
    events: List<ExecutorScheduleEvent>,
    onPrevWeek: () -> Unit,
    onNextWeek: () -> Unit,
    onOpenEvent: (Int) -> Unit,
) {
    val weekEnd = weekStart.plusDays(6)
    val byDay = events.groupBy { it.day }
    PanelCard {
        Text(
            "График на неделю",
            style = MaterialTheme.typography.titleMedium,
            color = VoitosColors.Accent2,
        )
        Spacer(modifier = Modifier.height(6.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TextButton(onClick = onPrevWeek) { Text("‹") }
            Text(
                "${weekStart.format(RANGE_FMT)} – ${weekEnd.format(RANGE_FMT)}",
                color = VoitosColors.Text,
                style = MaterialTheme.typography.titleSmall,
            )
            TextButton(onClick = onNextWeek) { Text("›") }
        }
        Spacer(modifier = Modifier.height(4.dp))
        for (i in 0..6) {
            val day = weekStart.plusDays(i.toLong())
            val key = day.toString()
            val dayEvents = byDay[key].orEmpty()
            Column(modifier = Modifier.padding(vertical = 4.dp)) {
                Text(
                    "${DAY_NAMES_RU[i]} · ${day.format(MONTH_FMT)}",
                    style = MaterialTheme.typography.labelLarge,
                    color = VoitosColors.Muted,
                )
                if (dayEvents.isEmpty()) {
                    Text("нет записей", color = VoitosColors.Muted.copy(alpha = 0.7f),
                        style = MaterialTheme.typography.bodySmall)
                } else {
                    dayEvents.forEach { ev ->
                        val timePart = formatEventTime(ev)
                        Column(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(top = 4.dp)
                                .background(
                                    VoitosColors.BgSoft.copy(alpha = 0.55f),
                                    RoundedCornerShape(10.dp),
                                )
                                .clickable(enabled = ev.workRequestId > 0) {
                                    onOpenEvent(ev.workRequestId)
                                }
                                .padding(horizontal = 10.dp, vertical = 8.dp),
                        ) {
                            Text(
                                timePart.ifBlank { ev.label }.ifBlank { "Визит" },
                                color = VoitosColors.Text,
                                style = MaterialTheme.typography.bodyMedium,
                            )
                            val subtitle = listOf(ev.roleName, ev.clientName)
                                .filter { it.isNotBlank() }
                                .joinToString(" · ")
                            if (subtitle.isNotBlank()) {
                                Text(subtitle, color = VoitosColors.Muted,
                                    style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun formatEventTime(ev: ExecutorScheduleEvent): String {
    fun takeHm(iso: String): String {
        val t = iso.substringAfter('T', missingDelimiterValue = "")
        return if (t.length >= 5) t.take(5) else ""
    }
    val a = takeHm(ev.startAt)
    val b = takeHm(ev.endAt)
    return when {
        a.isNotBlank() && b.isNotBlank() -> "$a–$b"
        ev.label.isNotBlank() -> ev.label
        else -> ""
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ProposeSlotPickerDialog(
    onDismiss: () -> Unit,
    onAdd: (String) -> Unit,
) {
    var selectedDate by remember { mutableStateOf(LocalDate.now().plusDays(1)) }
    var showCalendar by remember { mutableStateOf(false) }
    var startHour by remember { mutableIntStateOf(10) }
    var startMinute by remember { mutableIntStateOf(0) }
    var endHour by remember { mutableIntStateOf(12) }
    var endMinute by remember { mutableIntStateOf(0) }
    var localError by remember { mutableStateOf<String?>(null) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Окно визита") },
        text = {
            Column {
                Text("Дата", color = VoitosColors.Muted, style = MaterialTheme.typography.labelMedium)
                TextButton(onClick = { showCalendar = true }) {
                    Text(
                        selectedDate.format(
                            DateTimeFormatter.ofPattern("d MMMM yyyy", Locale("ru")),
                        ),
                        color = VoitosColors.Text,
                    )
                }
                Spacer(modifier = Modifier.height(8.dp))
                Text("Время", color = VoitosColors.Muted, style = MaterialTheme.typography.labelMedium)
                Spacer(modifier = Modifier.height(4.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceEvenly,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("Начало", color = VoitosColors.Muted, style = MaterialTheme.typography.labelSmall)
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            NumberScrollWheel(
                                value = startHour,
                                range = 0..23,
                                onValueChange = { startHour = it },
                            )
                            Text(":", color = VoitosColors.Text, modifier = Modifier.padding(horizontal = 2.dp))
                            NumberScrollWheel(
                                value = startMinute,
                                range = 0..55 step 5,
                                onValueChange = { startMinute = it },
                            )
                        }
                    }
                    Text("→", color = VoitosColors.Muted)
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("Конец", color = VoitosColors.Muted, style = MaterialTheme.typography.labelSmall)
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            NumberScrollWheel(
                                value = endHour,
                                range = 0..23,
                                onValueChange = { endHour = it },
                            )
                            Text(":", color = VoitosColors.Text, modifier = Modifier.padding(horizontal = 2.dp))
                            NumberScrollWheel(
                                value = endMinute,
                                range = 0..55 step 5,
                                onValueChange = { endMinute = it },
                            )
                        }
                    }
                }
                localError?.let {
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(it, color = VoitosColors.Danger, style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    val startMin = startHour * 60 + startMinute
                    val endMin = endHour * 60 + endMinute
                    if (endMin <= startMin) {
                        localError = "Конец должен быть позже начала"
                        return@TextButton
                    }
                    onAdd(
                        formatSlotLabel(
                            selectedDate,
                            startHour,
                            startMinute,
                            endHour,
                            endMinute,
                        ),
                    )
                },
            ) { Text("Добавить") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Отмена") }
        },
    )

    if (showCalendar) {
        val pickerState = rememberDatePickerState(
            initialSelectedDateMillis = selectedDate
                .atStartOfDay(ZoneOffset.UTC)
                .toInstant()
                .toEpochMilli(),
        )
        DatePickerDialog(
            onDismissRequest = { showCalendar = false },
            confirmButton = {
                TextButton(
                    onClick = {
                        val millis = pickerState.selectedDateMillis
                        if (millis != null) {
                            selectedDate = Instant.ofEpochMilli(millis)
                                .atZone(ZoneOffset.UTC)
                                .toLocalDate()
                        }
                        showCalendar = false
                    },
                ) { Text("ОК") }
            },
            dismissButton = {
                TextButton(onClick = { showCalendar = false }) { Text("Отмена") }
            },
        ) {
            DatePicker(state = pickerState)
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun NumberScrollWheel(
    value: Int,
    range: IntProgression,
    onValueChange: (Int) -> Unit,
) {
    val values = remember(range) { range.toList() }
    val initialIndex = values.indexOf(value).coerceAtLeast(0)
    val listState = rememberLazyListState(initialFirstVisibleItemIndex = initialIndex)
    val fling = rememberSnapFlingBehavior(lazyListState = listState)

    LaunchedEffect(listState) {
        snapshotFlow {
            listState.firstVisibleItemIndex +
                if (listState.firstVisibleItemScrollOffset > 20) 1 else 0
        }
            .distinctUntilChanged()
            .collect { idx ->
                val v = values.getOrNull(idx.coerceIn(0, values.lastIndex)) ?: return@collect
                if (v != value) onValueChange(v)
            }
    }

    Box(
        modifier = Modifier
            .height(120.dp)
            .width(48.dp),
        contentAlignment = Alignment.Center,
    ) {
        LazyColumn(
            state = listState,
            flingBehavior = fling,
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.fillMaxSize(),
        ) {
            items(values.size) { i ->
                val n = values[i]
                val selected = n == value
                Text(
                    text = "%02d".format(n),
                    modifier = Modifier
                        .height(40.dp)
                        .fillMaxWidth(),
                    textAlign = TextAlign.Center,
                    color = if (selected) VoitosColors.Text else VoitosColors.Muted,
                    style = if (selected) {
                        MaterialTheme.typography.titleMedium
                    } else {
                        MaterialTheme.typography.bodyLarge
                    },
                )
            }
        }
    }
}

private fun mondayOf(day: LocalDate): LocalDate =
    day.minusDays((day.dayOfWeek.value - 1).toLong())

private fun formatSlotLabel(
    date: LocalDate,
    startHour: Int,
    startMinute: Int,
    endHour: Int,
    endMinute: Int,
): String =
    "%02d.%02d.%04d %02d:%02d–%02d:%02d".format(
        date.dayOfMonth,
        date.monthValue,
        date.year,
        startHour,
        startMinute,
        endHour,
        endMinute,
    )
