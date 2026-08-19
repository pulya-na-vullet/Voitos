## Что уже есть

- `shared/` — модели, `VoitosApiClient` (Ktor), `DeepLinks`, `PushTokenProvider`
- `androidApp/` — Login (base URL + health), Inbox, Сборы, Заявки, фото, Подписка (+чек), Обучение (Coil), Confirm
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
