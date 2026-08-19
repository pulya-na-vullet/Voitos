package ru.voitos.app.push

/**
 * Абстракция FCM/APNs. Dev-сборка шлёт стабильный токен на сервер;
 * прод подключает Firebase Messaging + google-services.json.
 */
interface PushTokenProvider {
    suspend fun currentToken(): String?
}

/** Пока нет Firebase SDK — детерминированный токен для dry-run на сервере. */
class DevPushTokenProvider(
    private val deviceLabel: String,
    private val sessionSuffix: String,
) : PushTokenProvider {
    override suspend fun currentToken(): String =
        "dev-$deviceLabel-$sessionSuffix".take(120)
}
