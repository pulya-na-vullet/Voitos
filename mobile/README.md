# Voitos KMP (ветка `cursor/kmp-e31c`)

Нативный клиент без дублирования логики бота. Сервер: Django `/api/v1`.

## Что уже есть

- `shared/` — модели, `VoitosApiClient` (Ktor), `DeepLinks`
- `androidApp/` — skeleton Compose + deep link `voitos://app/...`
- Inbox на сервере: мастер назначен, нет мастера, confirm amount, сбор предложен

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
3. Collections  
4. Work request detail + confirm amount  
5. Subscription / Onboarding  
