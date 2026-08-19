# Чеклист: проверить Android-приложение

## Сборка

```bash
cd mobile
# Android Studio: Open → mobile/
# или:
./gradlew :androidApp:assembleDebug
```

APK: `androidApp/build/outputs/apk/debug/androidApp-debug.apk`

## Сервер

1. Запустить Voitos (`python app.py` / waitress на `:18765`)
2. В `.env`: `MOBILE_OTP_DEBUG=true`
3. В БД должен быть `BotUser` с телефоном, которым входите

## В приложении

1. **Login**
   - Base URL: эмулятор `http://10.0.2.2:18765/api/v1`, телефон `http://<LAN_IP>:18765/api/v1`
   - «Проверить сервер» → ✓
   - Телефон → получить код (dev-код на экране) → войти
2. **Inbox** — список уведомлений, кнопки разделов
3. **Сборы** → детали
4. **Вызвать мастера** → при роли с фото → добавить фото → «Готово»
5. **Подписка** → загрузить чек (или тестовый)
6. **Обучение** — 5 комиксов, «Понял · далее»
7. Deep link: `adb shell am start -a android.intent.action.VIEW -d "voitos://app/subscription"`

## Пока не обязательно

- Firebase / реальные push
- SMS OTP
- iOS
