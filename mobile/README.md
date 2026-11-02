# Voitos · приложение (Kotlin Multiplatform)

> **Источник кода:** репозиторий `pulya-na-vullet/Voitos`, ветка
> `cursor/test-app-backend-c5ef`, коммит `531ecb3` (07.09.2026) — из неё собран
> `../apk/voitos-debug.apk` (версия `0.2.41-kmp`). В ветке `main` каталога
> `mobile/` нет: приложение живёт только в ветках.
>
> **Модули:** `:shared` (KMP — `api/VoitosApiClient.kt` на Ktor,
> `model/Models.kt`, `nav/DeepLinks.kt`, `push/PushTokenProvider.kt`) и
> `:androidApp` (Compose UI — `MainActivity.kt`, `ui/AuthScreens.kt`,
> `ui/CabinetScreen.kt`, `ui/Screens.kt`, `update/ApkUpdater.kt`).
>
> **Сборка:** JDK 17+, Android SDK platform 35, `local.properties` с `sdk.dir`
> (в git не коммитится): `cd mobile && ./gradlew :androidApp:assembleDebug`

## Что уже есть

- `shared/` — модели, `VoitosApiClient` (Ktor), `DeepLinks`, `PushTokenProvider`
- `androidApp/` — Login (base URL + health), Inbox, Сборы, Заявки, фото, Подписка (+чек), Обучение (Coil), Confirm
- Личный кабинет → кнопка **«Голосования»** со счётчиком (`ui/BallotsScreen.kt`, данные из `/me` → `ballots.unvoted`)
- Вход по PIN на новом телефоне не выдаёт токен: экран переключается на код из MAX
  и показывает «Вход с нового телефона» (сервер отвечает `max_verification_required`)
- Gradle Wrapper: `./gradlew :androidApp:assembleDebug`
- После логина: `POST /devices` через `DevPushTokenProvider`
- Inbox API: мастер, сбор, confirm, чек, renewal, unpaid remind

## Документы

- [`CHECKLIST.md`](CHECKLIST.md) — как собрать и проверить
- [`../docs/mobile/`](../docs/mobile/) — OpenAPI / stories / push

## Сборка

```bash
cd mobile
./gradlew :androidApp:assembleDebug
```

Нужны JDK 17+ (проверено на 21) и Android SDK (platform 35).  
`local.properties` с `sdk.dir=...` создайте локально (в git не коммитится).

## Firebase

1. Добавить `androidApp/google-services.json`
2. В `androidApp/build.gradle.kts` раскомментировать plugin + firebase-messaging
3. Реализовать `PushTokenProvider` через `FirebaseMessaging.getInstance().token`
