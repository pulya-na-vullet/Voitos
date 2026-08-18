# Voitos KMP client (skeleton)

Стек: Kotlin Multiplatform + (Compose Multiplatform / нативный UI).

Сервер: Django `/api/v1` — см. `docs/mobile/`.

## Структура

```
mobile/
  shared/          # commonMain: API client, models, deep links
  androidApp/      # Android entry
  iosApp/          # iOS entry (позже)
```

## Первый срез экранов

1. Login (phone OTP)  
2. Home + inbox  
3. Collections (сборы / снег)  
4. Work requests (назначение мастера, confirm amount)  
5. Subscription  
6. Onboarding comics  

Пуши: FCM, `type` + `deep_link` из `docs/mobile/push-deeplinks.md`.

## Не дублировать в shared

Подписка/grace, матчинг мастеров, комиссия 10%, OCR чеков, награда за онбординг — только API.
