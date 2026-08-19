package ru.voitos.app.ui

import androidx.annotation.DrawableRes
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.boundsInRoot
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import ru.voitos.app.R
import ru.voitos.app.ui.theme.VoitosColors
import kotlin.math.roundToInt

enum class MainTab {
    Collections,
    WorkRequests,
    CallMaster,
    Cabinet,
    Work,
}

@Composable
fun VoitosBackground(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    colors = listOf(Color(0xFF10161D), Color(0xFF0B1015)),
                ),
            ),
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    Brush.radialGradient(
                        colors = listOf(Color(0x2E3D9CFD), Color.Transparent),
                        center = Offset(0f, 0f),
                        radius = 900f,
                    ),
                ),
        )
        // Тот же «подсвет» как у лого: бирюза в центре.
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    Brush.radialGradient(
                        colors = listOf(Color(0x332DD4BF), Color.Transparent),
                        center = Offset(0.5f, 0.42f),
                        radius = 700f,
                    ),
                ),
        )
        content()
    }
}

@Composable
fun MainShell(
    selected: MainTab,
    onSelect: (MainTab) -> Unit,
    playLogoSplash: Boolean,
    onSplashFinished: () -> Unit,
    showWorkTab: Boolean = false,
    content: @Composable () -> Unit,
) {
    var cabinetIconBounds by remember { mutableStateOf<Rect?>(null) }
    var splashVisible by remember { mutableStateOf(playLogoSplash) }
    var splashRunning by remember { mutableStateOf(playLogoSplash) }
    var showCabinetLabel by remember { mutableStateOf(!playLogoSplash) }

    BoxWithConstraints(modifier = Modifier.fillMaxSize()) {
        val screenW = constraints.maxWidth.toFloat()
        val screenH = constraints.maxHeight.toFloat()

        Column(modifier = Modifier.fillMaxSize()) {
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
            ) {
                Column(modifier = Modifier.fillMaxSize()) {
                    VpnDebugBanner(modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
                    Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
                        content()
                    }
                }
            }
            HorizontalDivider(color = VoitosColors.Line)
            BottomNavBar(
                selected = selected,
                onSelect = onSelect,
                showWorkTab = showWorkTab,
                hideCabinetIcon = splashRunning,
                showCabinetLabel = showCabinetLabel,
                onCabinetIconPositioned = { cabinetIconBounds = it },
            )
        }

        if (splashVisible) {
            LogoLaunchSplash(
                screenWidthPx = screenW,
                screenHeightPx = screenH,
                targetBounds = cabinetIconBounds,
                onRevealMain = {
                    // Главный экран уже под сплэшем; фон сплэша уходит в фазе 4.
                },
                onArrivedAtNav = {
                    showCabinetLabel = true
                },
                onFinished = {
                    splashRunning = false
                    splashVisible = false
                    showCabinetLabel = true
                    onSplashFinished()
                },
            )
        } else if (!playLogoSplash) {
            LaunchedEffect(Unit) { onSplashFinished() }
        }
    }
}

@Composable
private fun BottomNavBar(
    selected: MainTab,
    onSelect: (MainTab) -> Unit,
    showWorkTab: Boolean,
    hideCabinetIcon: Boolean,
    showCabinetLabel: Boolean,
    onCabinetIconPositioned: (Rect) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(VoitosColors.Bg.copy(alpha = 0.92f))
            .padding(horizontal = 2.dp, vertical = 8.dp)
            .height(64.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        NavItem(
            label = "Сборы",
            iconRes = R.drawable.ic_nav_home,
            selected = selected == MainTab.Collections,
            onClick = { onSelect(MainTab.Collections) },
            modifier = Modifier.weight(1f),
        )
        NavItem(
            label = "Заявки",
            iconRes = R.drawable.ic_nav_list,
            selected = selected == MainTab.WorkRequests,
            onClick = { onSelect(MainTab.WorkRequests) },
            modifier = Modifier.weight(1f),
        )
        NavItem(
            label = "Вызвать\nмастера",
            iconRes = R.drawable.ic_nav_engineer,
            selected = selected == MainTab.CallMaster,
            onClick = { onSelect(MainTab.CallMaster) },
            modifier = Modifier.weight(1f),
        )
        if (showWorkTab) {
            NavItem(
                label = "Работа",
                iconRes = R.drawable.ic_nav_work,
                selected = selected == MainTab.Work,
                onClick = { onSelect(MainTab.Work) },
                modifier = Modifier.weight(1f),
            )
        }
        NavItem(
            label = "Личный\nкабинет",
            iconRes = R.drawable.voitos_logo_nav,
            selected = selected == MainTab.Cabinet,
            onClick = { onSelect(MainTab.Cabinet) },
            modifier = Modifier.weight(1f),
            tintIcon = false,
            iconAlpha = if (hideCabinetIcon) 0f else 1f,
            labelAlpha = if (showCabinetLabel) 1f else 0f,
            onIconPositioned = onCabinetIconPositioned,
        )
    }
}

@Composable
private fun NavItem(
    label: String,
    @DrawableRes iconRes: Int,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    tintIcon: Boolean = true,
    iconAlpha: Float = 1f,
    labelAlpha: Float = 1f,
    onIconPositioned: ((Rect) -> Unit)? = null,
) {
    val accent = if (selected) VoitosColors.Accent2 else VoitosColors.Muted
    BoxWithConstraints(
        modifier = modifier
            .fillMaxHeight()
            .clickable(onClick = onClick)
            .padding(horizontal = 2.dp),
        contentAlignment = Alignment.Center,
    ) {
        val iconDp: Dp = (maxWidth * 0.42f).coerceIn(22.dp, 30.dp)
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Box(
                modifier = Modifier
                    .size(iconDp)
                    .then(
                        if (onIconPositioned != null) {
                            Modifier.onGloballyPositioned { coords ->
                                onIconPositioned(coords.boundsInRoot())
                            }
                        } else {
                            Modifier
                        },
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Image(
                    painter = painterResource(iconRes),
                    contentDescription = label.replace('\n', ' '),
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(1.dp),
                    contentScale = ContentScale.Fit,
                    alpha = iconAlpha,
                    colorFilter = if (tintIcon) ColorFilter.tint(accent) else null,
                )
            }
            Spacer(modifier = Modifier.height(2.dp))
            Text(
                text = label,
                color = accent.copy(alpha = accent.alpha * labelAlpha),
                fontSize = 10.sp,
                lineHeight = 11.sp,
                textAlign = TextAlign.Center,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

/**
 * Холодный старт:
 * 1) V по центру, 60% ширины
 * 2) через 0.5с → позиция 3/4 × 4/5, размер 25% ширины
 * 3) текст-слоган
 * 4) уход в слот «Личный кабинет» навбара
 */
@Composable
private fun LogoLaunchSplash(
    screenWidthPx: Float,
    screenHeightPx: Float,
    targetBounds: Rect?,
    onRevealMain: () -> Unit,
    onArrivedAtNav: () -> Unit,
    onFinished: () -> Unit,
) {
    val density = LocalDensity.current
    val safeW = screenWidthPx.coerceAtLeast(1f)
    val safeH = screenHeightPx.coerceAtLeast(1f)

    val sizeAnim = remember { Animatable(safeW * 0.60f) }
    val cxAnim = remember { Animatable(safeW / 2f) }
    val cyAnim = remember { Animatable(safeH / 2f) }
    val bgAnim = remember { Animatable(1f) }
    val textAnim = remember { Animatable(0f) }

    val midSize = safeW * 0.25f
    val midCx = safeW * 0.75f
    val midCy = safeH * 0.80f

    val boundsUpdated = rememberUpdatedState(targetBounds)
    val finishOnce = rememberUpdatedState(onFinished)
    val arrivedOnce = rememberUpdatedState(onArrivedAtNav)
    val revealOnce = rememberUpdatedState(onRevealMain)

    LaunchedEffect(Unit) {
        try {
            // 1. Держим крупный V (60% ширины, центр)
            delay(500)
            // 2. Уменьшение до 25% + сдвиг в 3/4 × 4/5
            coroutineScope {
                launch { sizeAnim.animateTo(midSize, tween(700, easing = FastOutSlowInEasing)) }
                launch { cxAnim.animateTo(midCx, tween(700, easing = FastOutSlowInEasing)) }
                launch { cyAnim.animateTo(midCy, tween(700, easing = FastOutSlowInEasing)) }
            }
            // 3. Слоган
            textAnim.animateTo(1f, tween(450))
            delay(900)
            // 4. Открываем главный (гасим фон) и летим в навбар
            revealOnce.value()
            val bounds = withTimeoutOrNull(900) {
                snapshotFlow { boundsUpdated.value }.filterNotNull().first()
            }
            val endSize = if (bounds != null && bounds.width > 1f) {
                minOf(bounds.width, bounds.height).coerceAtLeast(1f)
            } else {
                with(density) { 28.dp.toPx() }
            }
            val endCx = bounds?.center?.x ?: (safeW * 0.90f)
            val endCy = bounds?.center?.y ?: (safeH - with(density) { 36.dp.toPx() })

            coroutineScope {
                launch { bgAnim.animateTo(0f, tween(550)) }
                launch { textAnim.animateTo(0f, tween(350)) }
                launch { sizeAnim.animateTo(endSize, tween(650, easing = FastOutSlowInEasing)) }
                launch { cxAnim.animateTo(endCx, tween(650, easing = FastOutSlowInEasing)) }
                launch { cyAnim.animateTo(endCy, tween(650, easing = FastOutSlowInEasing)) }
            }
            arrivedOnce.value()
            delay(120)
        } catch (_: Exception) {
        } finally {
            finishOnce.value()
        }
    }

    val sizePx = sizeAnim.value.coerceAtLeast(1f)
    val cx = cxAnim.value
    val cy = cyAnim.value
    val bgA = bgAnim.value.coerceIn(0f, 1f)
    val textA = textAnim.value.coerceIn(0f, 1f)

    Box(modifier = Modifier.fillMaxSize()) {
        if (bgA > 0.01f) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(
                        Brush.verticalGradient(
                            colors = listOf(
                                Color(0xFF10161D).copy(alpha = bgA),
                                Color(0xFF0B1015).copy(alpha = bgA),
                            ),
                        ),
                    ),
            )
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(
                        Brush.radialGradient(
                            colors = listOf(
                                Color(0x552DD4BF).copy(alpha = 0.45f * bgA),
                                Color.Transparent,
                            ),
                            center = Offset(safeW * 0.5f, safeH * 0.42f),
                            radius = safeW * 0.85f,
                        ),
                    ),
            )
        }

        if (textA > 0.01f) {
            Text(
                text = "Voitos — ассистент вашего двора на базе ИИ",
                color = VoitosColors.Text.copy(alpha = textA),
                fontSize = 16.sp,
                lineHeight = 22.sp,
                textAlign = TextAlign.Center,
                modifier = Modifier
                    .align(Alignment.Center)
                    .padding(horizontal = 28.dp)
                    .offset(y = with(density) { (safeH * 0.08f).toDp() }),
            )
        }

        Image(
            painter = painterResource(R.drawable.voitos_logo_mark),
            contentDescription = null,
            modifier = Modifier
                .offset {
                    IntOffset(
                        (cx - sizePx / 2f).roundToInt(),
                        (cy - sizePx / 2f).roundToInt(),
                    )
                }
                .size(with(density) { sizePx.toDp() }),
            contentScale = ContentScale.Fit,
        )
    }
}

@Composable
fun PanelCard(
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(VoitosColors.Panel.copy(alpha = 0.86f), RoundedCornerShape(14.dp))
            .padding(14.dp),
    ) {
        content()
    }
}
