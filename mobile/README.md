# Voitos KMP (ветка `cursor/kmp-e31c`)

Нативный клиент без дублирования логики бота. Сервер: Django `/api/v1`.

## Что уже есть

- `shared/` — модели, `VoitosApiClient` (Ktor), `DeepLinks`, `PushTokenProvider`
- `androidApp/` — Login, Inbox, Сборы, Заявки, фото заявки, Вызов мастера, Подписка, Обучение, Confirm
- После логина: `POST /devices` через `DevPushTokenProvider` (Firebase — раскомментировать в gradle)
- Inbox: мастер, сбор, confirm, чек, renewal, unpaid remind
- API: `POST .../photos` (base64), `POST .../submit`, onboarding steps + caption

## Документы

См. [`../docs/mobile/`](../docs/mobile/)

## Открыть в IDE

```bash
cd mobile
# Android Studio → Open → mobile/
```

Нужен JDK 17+. Полный Gradle sync подтянет Ktor.

## Firebase

1. Добавить `androidApp/google-services.json`
2. В `androidApp/build.gradle.kts` раскомментировать plugin + firebase-messaging
3. Реализовать `PushTokenProvider` через `FirebaseMessaging.getInstance().token`

## Порядок экранов

1. Login (phone OTP)  
2. Inbox / Home  
3. Collections + detail  
4. New work request → photos → submit  
5. Subscription / Onboarding comics  
6. Confirm amount 
