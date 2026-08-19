# Voitos KMP (ветка `cursor/kmp-e31c`)

Нативный клиент без дублирования логики бота. Сервер: Django `/api/v1`.

## Что уже есть

- `shared/` — модели, `VoitosApiClient` (Ktor), `DeepLinks`
- `androidApp/` — Login, Inbox, Сборы (+detail), Заявки, Вызов мастера, Подписка, Confirm
- После логина: `POST /devices` с dev push-токеном (до Firebase SDK)
- Inbox на сервере: мастер, сбор, confirm, чек approve/reject, renewal, unpaid remind

## Документы

См. [`../docs/mobile/`](../docs/mobile/)

## Открыть в IDE

```bash
cd mobile
# Android Studio → Open → mobile/
```

Нужен JDK 17+. Полный Gradle sync подтянет Ktor.

## Порядок экранов

1. Login (phone OTP)  
2. Inbox / Home  
3. Collections + detail  
4. New work request / confirm amount  
5. Subscription  
6. Onboarding comics (API готов) 
